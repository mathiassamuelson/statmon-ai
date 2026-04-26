"""Tests for cli_executor stage-3 hardening:
kill-on-timeout, streaming output cap, sanitized env, prepend_args.
"""

import asyncio
import os
import stat
import textwrap
import time

import pytest

from statmon_mcp.catalog import ToolEntry
from statmon_mcp.cli_executor import SAFE_ENV, run_tool


def _entry(binary, *, prepend=None, timeout=10, max_bytes=65536):
    return ToolEntry(
        name="x",
        description="",
        binary_raw=binary,
        binary=binary,
        prepend_args=list(prepend or []),
        timeout_seconds=timeout,
        max_bytes=max_bytes,
        pipe_stage=False,
        rules={"deny": [], "allow": ["*"]},
    )


def _script(tmp_path, name, body):
    p = tmp_path / name
    p.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


@pytest.mark.asyncio
async def test_timeout_kills_subprocess(tmp_path):
    sleeper = _script(tmp_path, "sleeper", """
        import time
        time.sleep(30)
    """)
    entry = _entry(sleeper, timeout=1)
    start = time.monotonic()
    result = await run_tool(entry, "")
    elapsed = time.monotonic() - start
    assert result["status"] == "error"
    assert "timed out" in result["error"].lower()
    # Must actually have killed the subprocess, not just abandoned it.
    assert elapsed < 3.0


@pytest.mark.asyncio
async def test_output_cap_truncates_and_kills(tmp_path):
    flooder = _script(tmp_path, "flood", """
        import sys, time
        # Emit ~32KB then sit idle. Cap is 8KB so reader should detect
        # overflow, kill us, and not wait the 30s.
        sys.stdout.buffer.write(b'A' * 32768)
        sys.stdout.buffer.flush()
        time.sleep(30)
    """)
    entry = _entry(flooder, timeout=10, max_bytes=8192)
    start = time.monotonic()
    result = await run_tool(entry, "")
    elapsed = time.monotonic() - start
    # Was killed promptly even though the script would have slept 30s.
    assert elapsed < 3.0
    assert "[output truncated at 8192 bytes]" in result["result"]


@pytest.mark.asyncio
async def test_sanitized_env(tmp_path, monkeypatch):
    printer = _script(tmp_path, "printenv", """
        import json, os
        print(json.dumps(dict(os.environ)))
    """)
    monkeypatch.setenv("SECRET_TOKEN", "leaked-value")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leaked-aws")
    entry = _entry(printer)
    result = await run_tool(entry, "")
    assert result["status"] == "success"
    env = result["result"]
    # macOS injects __CF_USER_TEXT_ENCODING; we don't fight that. What matters
    # is that the SAFE_ENV keys are present with our values and the parent's
    # secrets did not leak.
    for k, v in SAFE_ENV.items():
        assert env[k] == v
    assert "SECRET_TOKEN" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env


@pytest.mark.asyncio
async def test_prepend_args_multi_element(tmp_path):
    echo = _script(tmp_path, "echo-argv", """
        import json, sys
        print(json.dumps(sys.argv[1:]))
    """)
    entry = _entry(echo, prepend=["one", "two"])
    result = await run_tool(entry, "three four=5")
    assert result["status"] == "success"
    assert result["result"] == ["one", "two", "three", "four=5"]


@pytest.mark.asyncio
async def test_prepend_args_empty(tmp_path):
    echo = _script(tmp_path, "echo-argv", """
        import json, sys
        print(json.dumps(sys.argv[1:]))
    """)
    entry = _entry(echo, prepend=[])
    result = await run_tool(entry, "alpha beta")
    assert result["status"] == "success"
    assert result["result"] == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_unhealthy_entry_returns_error():
    entry = ToolEntry(
        name="x", description="", binary_raw="missing", binary=None,
        prepend_args=[], timeout_seconds=5, max_bytes=1024, pipe_stage=False,
        rules={"deny": [], "allow": ["*"]},
        unhealthy_reason="binary 'missing' not found",
    )
    result = await run_tool(entry, "anything")
    assert result["status"] == "error"
    assert "missing" in result["error"]


@pytest.mark.asyncio
async def test_nonzero_exit_surfaces_stderr(tmp_path):
    failer = _script(tmp_path, "fail", """
        import sys
        print("oops on stderr", file=sys.stderr)
        sys.exit(2)
    """)
    entry = _entry(failer)
    result = await run_tool(entry, "")
    assert result["status"] == "error"
    assert result["exit_code"] == 2
    assert "oops on stderr" in result["error"]

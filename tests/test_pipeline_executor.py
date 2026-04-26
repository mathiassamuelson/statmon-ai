"""End-to-end tests for run_pipeline (multi-segment subprocess chain)."""

import stat
import textwrap
import time

import pytest

from statmon_mcp.catalog import ToolEntry
from statmon_mcp.cli_executor import run_pipeline


def _script(tmp_path, name, body):
    p = tmp_path / name
    p.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def _entry(name, binary, *, prepend=None, timeout=10, max_bytes=65536, pipe_stage=False):
    return ToolEntry(
        name=name, description="", binary_raw=binary, binary=binary,
        prepend_args=list(prepend or []), timeout_seconds=timeout,
        max_bytes=max_bytes, pipe_stage=pipe_stage,
        rules={"deny": [], "allow": ["*"]},
    )


@pytest.mark.asyncio
async def test_two_segment_pipeline_success(tmp_path):
    producer = _script(tmp_path, "producer", """
        print("alpha")
        print("beta")
        print("gamma")
    """)
    grep = _script(tmp_path, "grep-like", """
        import sys
        needle = sys.argv[1]
        for line in sys.stdin:
            if needle in line:
                sys.stdout.write(line)
    """)
    lead = _entry("producer", producer)
    stage = _entry("grep", grep, pipe_stage=True)
    out = await run_pipeline([(lead, ""), (stage, "alpha")])
    assert out["status"] == "success"
    assert out["result"] == "alpha"
    assert out["pipeline"] == [
        {"tool": "producer", "args": ""},
        {"tool": "grep", "args": "alpha"},
    ]


@pytest.mark.asyncio
async def test_three_segment_pipeline(tmp_path):
    producer = _script(tmp_path, "producer", """
        for i in range(10):
            print(f"line{i}")
    """)
    grep = _script(tmp_path, "grep-like", """
        import sys
        needle = sys.argv[1]
        for line in sys.stdin:
            if needle in line:
                sys.stdout.write(line)
    """)
    head = _script(tmp_path, "head", """
        import sys
        n = int(sys.argv[1])
        for i, line in enumerate(sys.stdin):
            if i >= n: break
            sys.stdout.write(line)
    """)
    lead = _entry("producer", producer)
    g = _entry("grep", grep, pipe_stage=True)
    h = _entry("head", head, pipe_stage=True)
    out = await run_pipeline([(lead, ""), (g, "line"), (h, "2")])
    assert out["status"] == "success"
    assert out["result"].splitlines() == ["line0", "line1"]


@pytest.mark.asyncio
async def test_pipeline_timeout_kills_all(tmp_path):
    sleeper = _script(tmp_path, "sleeper", """
        import sys, time
        # Emit something so downstream gets going, then hang.
        print("hello")
        sys.stdout.flush()
        time.sleep(30)
    """)
    cat = _script(tmp_path, "catlike", """
        import sys
        for line in sys.stdin:
            sys.stdout.write(line)
            sys.stdout.flush()
    """)
    lead = _entry("sleeper", sleeper, timeout=1)
    pipe = _entry("cat", cat, pipe_stage=True)
    start = time.monotonic()
    out = await run_pipeline([(lead, ""), (pipe, "")])
    elapsed = time.monotonic() - start
    assert out["status"] == "error"
    assert "timed out" in out["error"].lower()
    assert elapsed < 3.0


@pytest.mark.asyncio
async def test_non_last_nonzero_surfaces_warning(tmp_path):
    flaky = _script(tmp_path, "flaky", """
        import sys
        sys.stdout.write("payload\\n")
        sys.exit(2)  # non-zero, but produced output
    """)
    cat = _script(tmp_path, "catlike", """
        import sys
        for line in sys.stdin:
            sys.stdout.write(line)
    """)
    lead = _entry("flaky", flaky)
    pipe = _entry("cat", cat, pipe_stage=True)
    out = await run_pipeline([(lead, ""), (pipe, "")])
    assert out["status"] == "success"
    assert out["result"] == "payload"
    assert "warning" in out
    assert out["warning"][0]["tool"] == "flaky"
    assert out["warning"][0]["exit_code"] == 2


@pytest.mark.asyncio
async def test_unhealthy_segment(tmp_path):
    cat = _script(tmp_path, "catlike", """
        import sys
        for line in sys.stdin:
            sys.stdout.write(line)
    """)
    healthy = _entry("ok", cat)
    sick = ToolEntry(
        name="missing", description="", binary_raw="missing", binary=None,
        prepend_args=[], timeout_seconds=5, max_bytes=1024, pipe_stage=True,
        rules={"deny": [], "allow": ["*"]},
        unhealthy_reason="binary 'missing' not found",
    )
    out = await run_pipeline([(healthy, ""), (sick, "")])
    assert out["status"] == "error"
    assert "missing" in out["error"]

"""Tests for statmon_mcp.cli_executor — subprocess execution."""

import os
import stat
import tempfile
import textwrap
import pytest
from statmon_mcp.cli_executor import run_cli


@pytest.fixture
def cli_helper(tmp_path):
    """Create a small test helper script that echoes JSON based on args."""
    script = tmp_path / "test-cli"
    script.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json, sys
            args = sys.argv[1:]
            if not args:
                print("missing command", file=sys.stderr)
                sys.exit(1)
            cmd = args[0]
            kv = {}
            for a in args[1:]:
                if "=" in a:
                    k, v = a.split("=", 1)
                    kv[k] = v
            if cmd == "echo-json":
                print(json.dumps({"command": cmd, "args": kv}))
            elif cmd == "echo-text":
                print("hello world")
            else:
                print(f"unknown command: {cmd}", file=sys.stderr)
                sys.exit(1)
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


@pytest.fixture(autouse=True)
def set_node_name(monkeypatch):
    monkeypatch.setenv("NODE_NAME", "test-node")


@pytest.mark.asyncio
async def test_basic_json_command(cli_helper):
    result = await run_cli(cli_helper, "", "echo-json foo=bar", timeout=10)
    assert result["status"] == "success"
    assert result["exit_code"] == 0
    assert result["result"] == {"command": "echo-json", "args": {"foo": "bar"}}
    assert result["execution_time_ms"] >= 0


@pytest.mark.asyncio
async def test_plain_text_output(cli_helper):
    result = await run_cli(cli_helper, "", "echo-text", timeout=10)
    assert result["status"] == "success"
    assert result["result"] == "hello world"


@pytest.mark.asyncio
async def test_subsystem_prepended(cli_helper):
    """When subsystem is set, it's prepended as the first argument."""
    result = await run_cli(cli_helper, "mysub", "echo-json", timeout=10)
    # The helper sees 'mysub' as the command, not 'echo-json', so it fails.
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_empty_subsystem_not_prepended(cli_helper):
    result = await run_cli(cli_helper, "", "echo-json key=val", timeout=10)
    assert result["status"] == "success"
    assert result["result"]["args"] == {"key": "val"}


@pytest.mark.asyncio
async def test_unknown_command(cli_helper):
    result = await run_cli(cli_helper, "", "nonexistent", timeout=10)
    assert result["status"] == "error"
    assert result["exit_code"] != 0


@pytest.mark.asyncio
async def test_binary_not_found():
    result = await run_cli("/nonexistent/binary", "", "echo-json", timeout=10)
    assert result["status"] == "error"
    assert "not found" in result["error"].lower()


@pytest.mark.asyncio
async def test_timeout():
    result = await run_cli("sleep", "", "10", timeout=1)
    assert result["status"] == "error"
    assert "timed out" in result["error"].lower()


@pytest.mark.asyncio
async def test_multiple_key_value_args(cli_helper):
    result = await run_cli(
        cli_helper, "", "echo-json duration=3600 max-results=5", timeout=10
    )
    assert result["status"] == "success"
    assert result["result"]["args"] == {"duration": "3600", "max-results": "5"}


@pytest.mark.asyncio
async def test_quoted_args_with_spaces(cli_helper):
    """Quoted arguments with spaces are handled correctly by shlex."""
    result = await run_cli(
        cli_helper, "", 'echo-json filter="((query-type (true (A AAAA))))"', timeout=10
    )
    assert result["status"] == "success"
    assert "filter" in result["result"]["args"]

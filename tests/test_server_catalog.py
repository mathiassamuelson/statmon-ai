"""Integration tests for server.py dispatching through the catalog."""

import json
import stat
import textwrap

import pytest

from statmon_mcp import server as srv


@pytest.fixture
def stub_env(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "fakebin"
    fake.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json, sys
            print(json.dumps({"argv": sys.argv[1:]}))
            """
        )
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    catalog = tmp_path / "catalog"
    catalog.mkdir()
    (catalog / "fake.yaml").write_text(
        textwrap.dedent(
            """\
            - name: fake
              description: Fake tool.
              binary: fakebin
              prepend_args: ["sub"]
              timeout_seconds: 5
              rules:
                deny:
                  - "destroy*"
                allow:
                  - "ok*"
            - name: missing
              description: Missing tool.
              binary: not-installed
              rules:
                allow: ["*"]
            """
        )
    )

    cfg = {
        "server": {"host": "0.0.0.0", "port": 8100, "node_name": "test-node"},
        "catalog": {
            "path": str(catalog),
            "search_paths": [str(bin_dir)],
            "defaults": {"timeout_seconds": 10},
        },
    }
    monkeypatch.setattr(srv, "CONFIG", cfg)
    monkeypatch.setattr(srv, "REGISTRY", None)
    yield
    monkeypatch.setattr(srv, "REGISTRY", None)


@pytest.mark.asyncio
async def test_list_tools_from_catalog(stub_env):
    tools = await srv.list_tools()
    names = sorted(t.name for t in tools)
    assert names == ["fake", "missing"]
    fake = next(t for t in tools if t.name == "fake")
    assert fake.description == "Fake tool."
    assert "command" in fake.inputSchema["properties"]


@pytest.mark.asyncio
async def test_call_tool_dispatches_with_prepend_args(stub_env):
    out = await srv.call_tool("fake", {"command": "ok hello"})
    payload = json.loads(out[0]["text"])
    assert payload["status"] == "success"
    assert payload["node"] == "test-node"
    assert payload["tool"] == "fake"
    assert payload["result"]["argv"] == ["sub", "ok", "hello"]


@pytest.mark.asyncio
async def test_call_tool_denied(stub_env):
    out = await srv.call_tool("fake", {"command": "destroy everything"})
    payload = json.loads(out[0]["text"])
    assert payload["status"] == "denied"
    assert "deny" in payload["error"].lower()


@pytest.mark.asyncio
async def test_call_tool_unknown(stub_env):
    out = await srv.call_tool("nope", {"command": "x"})
    payload = json.loads(out[0]["text"])
    assert payload["status"] == "error"
    assert "Unknown tool" in payload["error"]


@pytest.mark.asyncio
async def test_call_tool_unhealthy(stub_env):
    out = await srv.call_tool("missing", {"command": "anything"})
    payload = json.loads(out[0]["text"])
    assert payload["status"] == "error"
    assert "unavailable" in payload["error"].lower()
    assert "not-installed" in payload["error"]

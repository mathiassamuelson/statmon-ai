"""Tests for statmon_chat.mcp_pool — tool registry and routing."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace

from statmon_chat.mcp_pool import MCPPool


@pytest.fixture
def pool_with_mock_nodes():
    """Create an MCPPool with manually injected mock nodes."""
    pool = MCPPool()

    mock_tool = SimpleNamespace(
        name="statmon",
        description="Execute a Statmon querystore command",
        inputSchema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command"}
            },
            "required": ["command"],
        },
    )

    node_a = SimpleNamespace(
        name="dns-node-a",
        mcp_url="http://mcp-node-a:8100/mcp",
        session=MagicMock(),
        tools=[mock_tool],
    )
    node_b = SimpleNamespace(
        name="dns-node-b",
        mcp_url="http://mcp-node-b:8100/mcp",
        session=MagicMock(),
        tools=[mock_tool],
    )

    pool._nodes["dns-node-a"] = node_a
    pool._nodes["dns-node-b"] = node_b
    pool._tool_registry["dns_node_a__statmon"] = (node_a, "statmon")
    pool._tool_registry["dns_node_b__statmon"] = (node_b, "statmon")
    pool._node_configs = {
        "dns-node-a": {"name": "dns-node-a", "mcp_url": "http://mcp-node-a:8100/mcp"},
        "dns-node-b": {"name": "dns-node-b", "mcp_url": "http://mcp-node-b:8100/mcp"},
    }

    return pool


class TestToolRegistry:
    def test_build_anthropic_tools(self, pool_with_mock_nodes):
        tools = pool_with_mock_nodes.build_anthropic_tools()
        assert len(tools) == 2

        names = {t["name"] for t in tools}
        assert "dns_node_a__statmon" in names
        assert "dns_node_b__statmon" in names

        for tool in tools:
            assert "[Node:" in tool["description"]
            assert "input_schema" in tool

    def test_get_node_list(self, pool_with_mock_nodes):
        nodes = pool_with_mock_nodes.get_node_list()
        assert len(nodes) == 2
        names = {n["name"] for n in nodes}
        assert "dns-node-a" in names
        assert "dns-node-b" in names
        for node in nodes:
            assert node["status"] == "connected"
            assert len(node["tools"]) == 1


class TestToolRouting:
    @pytest.mark.asyncio
    async def test_call_routes_to_correct_node(self, pool_with_mock_nodes):
        mock_result = MagicMock()
        mock_result.content = [SimpleNamespace(text='{"count": 100}')]

        node_a = pool_with_mock_nodes._nodes["dns-node-a"]
        node_a.session.call_tool = AsyncMock(return_value=mock_result)

        result = await pool_with_mock_nodes.call_tool(
            "dns_node_a__statmon", {"command": "querystore.count"}
        )

        assert result == '{"count": 100}'
        node_a.session.call_tool.assert_called_once_with(
            "statmon", {"command": "querystore.count"}
        )

    @pytest.mark.asyncio
    async def test_call_unknown_tool_raises(self, pool_with_mock_nodes):
        with pytest.raises(ValueError, match="Unknown tool"):
            await pool_with_mock_nodes.call_tool(
                "nonexistent__tool", {"command": "test"}
            )

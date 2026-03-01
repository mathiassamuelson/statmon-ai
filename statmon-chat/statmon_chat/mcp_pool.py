"""MCP client pool managing connections to all nodes.

Handles tool discovery, node-prefixed naming, and tool call routing.
Uses AsyncExitStack to manage persistent SSE connections.
"""

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

from mcp import ClientSession
from mcp.client.sse import sse_client

logger = logging.getLogger(__name__)


@dataclass
class NodeConnection:
    name: str
    mcp_url: str
    session: ClientSession
    tools: list = field(default_factory=list)


class MCPPool:
    def __init__(self):
        self._nodes: dict[str, NodeConnection] = {}
        self._tool_registry: dict[str, tuple[NodeConnection, str]] = {}
        self._exit_stack = AsyncExitStack()

    @property
    def nodes(self) -> dict[str, NodeConnection]:
        return self._nodes

    @property
    def tool_registry(self) -> dict[str, tuple[NodeConnection, str]]:
        return self._tool_registry

    async def connect_all(self, nodes_config: list[dict]) -> None:
        """Connect to all configured MCP nodes."""
        for node_cfg in nodes_config:
            name = node_cfg["name"]
            url = node_cfg["mcp_url"]
            try:
                await self._connect_node(name, url)
                logger.info(f"Connected to {name} at {url}")
            except Exception:
                logger.exception(f"Failed to connect to {name} at {url}")

    async def _connect_node(self, name: str, url: str) -> None:
        """Connect to a single MCP node and discover its tools."""
        streams = await self._exit_stack.enter_async_context(
            sse_client(url)
        )
        session = await self._exit_stack.enter_async_context(
            ClientSession(*streams)
        )
        await session.initialize()

        tools_response = await session.list_tools()
        node = NodeConnection(
            name=name,
            mcp_url=url,
            session=session,
            tools=tools_response.tools,
        )
        self._nodes[name] = node

        for tool in tools_response.tools:
            prefixed = f"{name.replace('-', '_')}__{tool.name}"
            self._tool_registry[prefixed] = (node, tool.name)

    def build_anthropic_tools(self) -> list[dict]:
        """Convert MCP tool definitions to Anthropic API tool format."""
        tools = []
        for prefixed_name, (node, original_name) in self._tool_registry.items():
            mcp_tool = next(t for t in node.tools if t.name == original_name)
            tools.append(
                {
                    "name": prefixed_name,
                    "description": f"[Node: {node.name}] {mcp_tool.description}",
                    "input_schema": mcp_tool.inputSchema,
                }
            )
        return tools

    def get_node_list(self) -> list[dict]:
        """Return a summary of connected nodes and their tools."""
        result = []
        for name, node in self._nodes.items():
            tool_names = [
                f"{name.replace('-', '_')}__{t.name}" for t in node.tools
            ]
            result.append(
                {
                    "name": name,
                    "mcp_url": node.mcp_url,
                    "tools": tool_names,
                    "status": "connected",
                }
            )
        return result

    async def call_tool(self, prefixed_name: str, arguments: dict) -> str:
        """Route a tool call to the correct MCP server."""
        if prefixed_name not in self._tool_registry:
            raise ValueError(f"Unknown tool: {prefixed_name}")

        node, original_name = self._tool_registry[prefixed_name]
        result = await node.session.call_tool(original_name, arguments)

        if result.content and hasattr(result.content[0], "text"):
            return result.content[0].text
        return str(result.content)

    async def disconnect_all(self) -> None:
        """Clean shutdown of all MCP connections."""
        await self._exit_stack.aclose()
        self._nodes.clear()
        self._tool_registry.clear()

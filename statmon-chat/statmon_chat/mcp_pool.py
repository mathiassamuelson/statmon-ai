"""MCP client pool managing connections to all nodes.

Handles tool discovery, node-prefixed naming, tool call routing,
health checking, and automatic reconnection.
"""

import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

from mcp import ClientSession
from mcp.client.sse import sse_client

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 15
HEALTH_CHECK_TIMEOUT_SECONDS = 5
TOOL_CALL_TIMEOUT_SECONDS = 120


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
        self._node_configs: dict[str, dict] = {}
        self._exit_stacks: dict[str, AsyncExitStack] = {}

    @property
    def nodes(self) -> dict[str, NodeConnection]:
        return self._nodes

    @property
    def tool_registry(self) -> dict[str, tuple[NodeConnection, str]]:
        return self._tool_registry

    @property
    def node_configs(self) -> dict[str, dict]:
        return self._node_configs

    async def connect_all(self, nodes_config: list[dict]) -> None:
        """Connect to all configured MCP nodes."""
        for node_cfg in nodes_config:
            name = node_cfg["name"]
            self._node_configs[name] = node_cfg
            try:
                await asyncio.wait_for(
                    self._connect_node(name, node_cfg["mcp_url"]),
                    timeout=CONNECT_TIMEOUT_SECONDS,
                )
                logger.info(f"Connected to {name} at {node_cfg['mcp_url']}")
            except asyncio.TimeoutError:
                logger.error(
                    f"Timed out connecting to {name} at {node_cfg['mcp_url']} "
                    f"(>{CONNECT_TIMEOUT_SECONDS}s) — skipping"
                )
            except Exception as exc:
                # Extract the root cause from ExceptionGroups
                cause = exc
                while cause.__cause__:
                    cause = cause.__cause__
                logger.warning(
                    f"Failed to connect to {name} at {node_cfg['mcp_url']}"
                    f" — {cause}"
                )

    async def _connect_node(self, name: str, url: str) -> None:
        """Connect to a single MCP node and discover its tools."""
        # Close existing connection if any
        if name in self._exit_stacks:
            await self._close_node(name)

        stack = AsyncExitStack()
        streams = await stack.enter_async_context(sse_client(url))
        session = await stack.enter_async_context(ClientSession(*streams))
        await session.initialize()

        tools_response = await session.list_tools()
        node = NodeConnection(
            name=name,
            mcp_url=url,
            session=session,
            tools=tools_response.tools,
        )
        self._nodes[name] = node
        self._exit_stacks[name] = stack

        for tool in tools_response.tools:
            prefixed = f"{name.replace('-', '_')}__{tool.name}"
            self._tool_registry[prefixed] = (node, tool.name)

    async def _close_node(self, name: str) -> None:
        """Close connection to a single node and clean up its registry entries."""
        stale_keys = [
            k
            for k, (node, _) in self._tool_registry.items()
            if node.name == name
        ]
        for k in stale_keys:
            del self._tool_registry[k]

        self._nodes.pop(name, None)

        stack = self._exit_stacks.pop(name, None)
        if stack:
            try:
                await stack.aclose()
            except BaseException:
                # anyio task group teardown can raise BaseExceptionGroup or
                # RuntimeError ("cancel scope in different task") — both are
                # harmless during connection cleanup.
                logger.debug(f"Error closing connection to {name}", exc_info=True)

    async def _try_reconnect(self, name: str) -> bool:
        """Attempt to reconnect to a node. Returns True on success."""
        cfg = self._node_configs.get(name)
        if not cfg:
            return False
        try:
            await asyncio.wait_for(
                self._connect_node(name, cfg["mcp_url"]),
                timeout=CONNECT_TIMEOUT_SECONDS,
            )
            logger.info(f"Reconnected to {name}")
            return True
        except Exception:
            logger.warning(f"Reconnection failed for {name}")
            await self._close_node(name)
            return False

    async def check_health(self) -> dict[str, bool]:
        """Check health of all configured nodes, reconnecting if needed.

        Returns a dict mapping node name to health status (True=healthy).
        """
        results = {}
        for name in self._node_configs:
            if name in self._nodes:
                try:
                    await asyncio.wait_for(
                        self._nodes[name].session.list_tools(),
                        timeout=HEALTH_CHECK_TIMEOUT_SECONDS,
                    )
                    results[name] = True
                except Exception:
                    logger.warning(
                        f"Health check failed for {name}, attempting reconnect"
                    )
                    results[name] = await self._try_reconnect(name)
            else:
                results[name] = await self._try_reconnect(name)
        return results

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
        """Return a summary of all configured nodes and their status."""
        result = []
        for name, cfg in self._node_configs.items():
            connected = name in self._nodes
            node = self._nodes.get(name)
            tool_names = []
            if node:
                tool_names = [
                    f"{name.replace('-', '_')}__{t.name}" for t in node.tools
                ]
            result.append(
                {
                    "name": name,
                    "mcp_url": cfg["mcp_url"],
                    "tools": tool_names,
                    "status": "connected" if connected else "disconnected",
                }
            )
        return result

    async def call_tool(self, prefixed_name: str, arguments: dict) -> str:
        """Route a tool call to the correct MCP server.

        On connection failure, attempts one reconnect before raising.
        """
        if prefixed_name not in self._tool_registry:
            raise ValueError(f"Unknown tool: {prefixed_name}")

        node, original_name = self._tool_registry[prefixed_name]
        try:
            result = await asyncio.wait_for(
                node.session.call_tool(original_name, arguments),
                timeout=TOOL_CALL_TIMEOUT_SECONDS,
            )
        except Exception as e:
            logger.warning(
                f"Tool call failed on {node.name}, attempting reconnect: {e}"
            )
            reconnected = await self._try_reconnect(node.name)
            if not reconnected or prefixed_name not in self._tool_registry:
                raise
            node, original_name = self._tool_registry[prefixed_name]
            result = await asyncio.wait_for(
                node.session.call_tool(original_name, arguments),
                timeout=TOOL_CALL_TIMEOUT_SECONDS,
            )

        if result.content and hasattr(result.content[0], "text"):
            return result.content[0].text
        return str(result.content)

    async def disconnect_all(self) -> None:
        """Clean shutdown of all MCP connections.

        Installs a custom event loop exception handler to suppress the noisy
        tracebacks that the MCP SSE async generators produce when torn down
        during shutdown (BaseExceptionGroup / RuntimeError from anyio cancel
        scopes).  These errors are reported via loop.call_exception_handler()
        from loop.shutdown_asyncgens(), not through Python logging.
        """
        loop = asyncio.get_running_loop()
        original_handler = loop.get_exception_handler()

        def _quiet_handler(loop, context):
            msg = context.get("message", "")
            if "asynchronous generator" in msg:
                return  # suppress MCP SSE asyncgen teardown noise
            # Fall through to default or original handler for anything else
            if original_handler is not None:
                original_handler(loop, context)
            else:
                loop.default_exception_handler(context)

        loop.set_exception_handler(_quiet_handler)

        for name in list(self._exit_stacks.keys()):
            await self._close_node(name)
        self._node_configs.clear()

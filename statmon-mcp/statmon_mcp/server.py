"""MCP server exposing catalog-driven CLI tools via SSE transport.

Loads config from STATMON_MCP_CONFIG env var, ~/.config/statmon-mcp/config.yaml,
or /etc/statmon-mcp/config.yaml. Tools are declared as YAML entries in the
catalog directory (config["catalog"]["path"]); the server registers one MCP
Tool per entry and dispatches call_tool() through the registry.
"""

import json
import os
from pathlib import Path

import yaml
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.responses import JSONResponse

from .catalog import load_catalog, ToolRegistry
from .filter import check_command, check_paths
from .cli_executor import run_tool, run_pipeline
from .pipeline import (
    parse_pipeline,
    resolve_pipeline,
    PipelineGrammarError,
    PipelineResolutionError,
)


DEFAULT_SEARCH_PATHS = [
    "/usr/local/sbin",
    "/usr/local/bin",
    "/usr/sbin",
    "/usr/bin",
    "/sbin",
    "/bin",
    "/usr/local/nom/sbin",
]


def load_config() -> dict:
    config_path = os.environ.get("STATMON_MCP_CONFIG")
    if not config_path:
        user_path = Path.home() / ".config" / "statmon-mcp" / "config.yaml"
        config_path = str(user_path) if user_path.exists() else "/etc/statmon-mcp/config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


CONFIG: dict | None = None
REGISTRY: ToolRegistry | None = None
mcp_server = Server("statmon-mcp")
sse_transport = SseServerTransport("/messages/")


def get_config() -> dict:
    global CONFIG
    if CONFIG is None:
        CONFIG = load_config()
    return CONFIG


def get_registry() -> ToolRegistry:
    global REGISTRY
    if REGISTRY is None:
        cat_cfg = get_config().get("catalog") or {}
        REGISTRY = load_catalog(
            cat_cfg.get("path", "/etc/statmon-mcp/catalog/"),
            defaults=cat_cfg.get("defaults") or {},
            search_paths=cat_cfg.get("search_paths") or DEFAULT_SEARCH_PATHS,
        )
    return REGISTRY


def _envelope(node: str, name: str, command: str | None, **rest) -> list[dict]:
    payload = {"node": node, "tool": name}
    if command is not None:
        payload["command"] = command
    payload.update(rest)
    return [{"type": "text", "text": json.dumps(payload)}]


@mcp_server.list_tools()
async def list_tools():
    registry = get_registry()
    return [
        Tool(
            name=entry.name,
            description=entry.description,
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "Arguments to pass to the tool. May include pipes to other catalog "
                            "tools flagged as pipe stages (e.g., `aux | grep nginx | head -5`)."
                        ),
                    }
                },
                "required": ["command"],
            },
        )
        for entry in registry
    ]


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict):
    config = get_config()
    node_name = config["server"]["node_name"]
    registry = get_registry()

    entry = registry.get(name)
    if entry is None:
        return _envelope(node_name, name, None, status="error", error=f"Unknown tool: {name}")

    command = arguments.get("command", "")

    if not entry.healthy:
        return _envelope(
            node_name, name, command,
            status="error",
            error=f"Tool unavailable on this node: {entry.unhealthy_reason}",
        )

    try:
        segments = parse_pipeline(command)
    except PipelineGrammarError as e:
        return _envelope(node_name, name, command, status="denied", error=str(e))

    try:
        stages = resolve_pipeline(entry, segments, registry)
    except PipelineResolutionError as e:
        return _envelope(node_name, name, command, status="denied", error=str(e))

    for i, (seg_entry, seg_args) in enumerate(stages):
        if not seg_entry.healthy:
            return _envelope(
                node_name, name, command,
                status="error",
                error=f"segment {i} ({seg_entry.name}): {seg_entry.unhealthy_reason}",
            )
        allowed, reason = check_command(seg_args, seg_entry.rules)
        if not allowed:
            return _envelope(
                node_name, name, command,
                status="denied",
                error=f"segment {i} ({seg_entry.name}): {reason}",
            )
        ok, preason = check_paths(seg_args, seg_entry.path_deny)
        if not ok:
            return _envelope(
                node_name, name, command,
                status="denied",
                error=f"segment {i} ({seg_entry.name}): {preason}",
            )

    if len(stages) == 1:
        result = await run_tool(entry, command)
    else:
        result = await run_pipeline(stages)

    result["node"] = node_name
    result["tool"] = name
    result["command"] = command
    return [{"type": "text", "text": json.dumps(result)}]


async def handle_sse(scope, receive, send):
    async with sse_transport.connect_sse(scope, receive, send) as streams:
        await mcp_server.run(
            streams[0], streams[1], mcp_server.create_initialization_options()
        )


async def handle_messages(scope, receive, send):
    await sse_transport.handle_post_message(scope, receive, send)


async def health(request):
    config = get_config()
    registry = get_registry()
    tools = [
        {"name": e.name, "healthy": e.healthy, **({"reason": e.unhealthy_reason} if not e.healthy else {})}
        for e in registry
    ]
    return JSONResponse(
        {
            "status": "ok",
            "node": config["server"]["node_name"],
            "tools": tools,
        }
    )


app = Starlette(
    routes=[
        Route("/health", health),
        Mount("/mcp/messages", app=handle_messages),
        Mount("/mcp", app=handle_sse),
    ],
)

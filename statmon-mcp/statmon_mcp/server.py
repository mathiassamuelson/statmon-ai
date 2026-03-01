"""MCP server exposing Statmon CLI tools via SSE transport.

Loads config from STATMON_MCP_CONFIG env var, ~/.config/statmon-mcp/config.yaml, or /etc/statmon-mcp/config.yaml.
Exposes a single 'statmon' tool (CacheServe deferred).
"""

import json
import os

import yaml
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.responses import JSONResponse

from .filter import check_command
from .cli_executor import run_cli


def load_config() -> dict:
    from pathlib import Path

    config_path = os.environ.get("STATMON_MCP_CONFIG")
    if not config_path:
        user_path = Path.home() / ".config" / "statmon-mcp" / "config.yaml"
        config_path = str(user_path) if user_path.exists() else "/etc/statmon-mcp/config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


CONFIG = None
mcp_server = Server("statmon-mcp")
sse_transport = SseServerTransport("/messages/")


def get_config() -> dict:
    global CONFIG
    if CONFIG is None:
        CONFIG = load_config()
    return CONFIG


@mcp_server.list_tools()
async def list_tools():
    config = get_config()
    node_name = config["server"]["node_name"]
    return [
        Tool(
            name="statmon",
            description=(
                f"Execute a read-only Statmon querystore command on node {node_name}. "
                "Returns JSON output from the Statmon log collector. "
                "Use for query activity analysis, traffic metrics, bandwidth statistics, "
                "and forensic replay of DNS queries. "
                "Commands use S-expression filter syntax for targeted searches."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "The Statmon querystore command to execute "
                            "(e.g., 'querystore.top-clients duration=3600 max-results=10', "
                            "'querystore.count duration=300 "
                            'filter="((result-code (true (nxdomain))))"\')'
                        ),
                    }
                },
                "required": ["command"],
            },
        )
    ]


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict):
    config = get_config()
    node_name = config["server"]["node_name"]

    if name != "statmon":
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "node": node_name,
                        "tool": name,
                        "status": "error",
                        "error": f"Unknown tool: {name}",
                    }
                ),
            }
        ]

    command = arguments.get("command", "")
    cfg = config["statmon"]

    allowed, reason = check_command(command, cfg["rules"])
    if not allowed:
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "node": node_name,
                        "tool": "statmon",
                        "command": command,
                        "status": "denied",
                        "error": reason,
                    }
                ),
            }
        ]

    result = await run_cli(
        cfg["binary"], cfg.get("subsystem", ""), command, cfg["timeout_seconds"]
    )
    result["node"] = node_name
    result["tool"] = "statmon"
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
    return JSONResponse(
        {
            "status": "ok",
            "node": config["server"]["node_name"],
            "tools": ["statmon"],
        }
    )


app = Starlette(
    routes=[
        Route("/health", health),
        Mount("/mcp/messages", app=handle_messages),
        Mount("/mcp", app=handle_sse),
    ],
)

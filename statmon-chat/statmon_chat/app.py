"""FastAPI web application for the Statmon AI chat interface.

Manages MCP connections, session state, and routes for the chat API.
"""

import logging
import time
import uuid
from contextlib import asynccontextmanager

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .anthropic_client import AnthropicChat
from .mcp_pool import MCPPool
from .security_tools import get_tool_definitions as get_security_tools
from .system_prompt import build_system_prompt
from .trace import TraceCollector

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 3600  # 1 hour


class _SSEDisconnectFilter(logging.Filter):
    """Downgrade the noisy MCP SSE traceback to a short warning.

    When an MCP server shuts down, the SSE reader in the mcp library logs a
    full exception traceback at ERROR level ("Error in sse_reader").  This
    filter intercepts that record and replaces it with a one-line WARNING so
    the log stays readable.  The reconnect logic in MCPPool handles recovery.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.funcName == "sse_reader" and record.exc_info:
            exc = record.exc_info[1]
            msg = str(exc) if exc else record.getMessage()
            logger.warning(
                "MCP node SSE connection lost (%s) — "
                "will reconnect on next health check or tool call",
                msg,
            )
            return False  # suppress the original noisy record
        return True


logging.getLogger("mcp.client.sse").addFilter(_SSEDisconnectFilter())


class _CancelledErrorFilter(logging.Filter):
    """Suppress CancelledError tracebacks from starlette/uvicorn during reconnect.

    When an MCP SSE connection is torn down for reconnection, the anyio task
    group cancellation propagates as a CancelledError through starlette's
    lifespan handler.  Starlette formats the traceback as a plain string and
    sends it to uvicorn which logs it at ERROR level (no exc_info — just the
    traceback text in the message body).  This is expected and harmless.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "CancelledError" in msg:
            return False
        return True


logging.getLogger("uvicorn.error").addFilter(_CancelledErrorFilter())


def load_config() -> dict:
    import os
    from pathlib import Path

    config_path = os.environ.get("STATMON_CHAT_CONFIG")
    if not config_path:
        user_path = Path.home() / ".config" / "statmon-chat" / "config.yaml"
        config_path = str(user_path) if user_path.exists() else "/etc/statmon-chat/config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


# Global state set during lifespan
_mcp_pool: MCPPool | None = None
_anthropic_chat: AnthropicChat | None = None
_prompt_path: str | None = None
_security_tools_config: dict = {}
_sessions: dict[str, dict] = {}  # session_id -> {"messages": [...], "last_access": float}


def _evict_stale_sessions():
    now = time.time()
    stale = [
        sid
        for sid, data in _sessions.items()
        if now - data["last_access"] > SESSION_TTL_SECONDS
    ]
    for sid in stale:
        del _sessions[sid]


def _build_tools() -> list[dict]:
    """Build the combined tool list from the current MCP pool state."""
    mcp_tools = _mcp_pool.build_anthropic_tools() if _mcp_pool else []
    return (
        mcp_tools
        + get_security_tools()
        + [{"type": "web_search_20250305", "name": "web_search"}]
    )


def _build_system_prompt() -> str:
    """Build the system prompt from the current MCP pool state."""
    node_list = _mcp_pool.get_node_list() if _mcp_pool else []
    return build_system_prompt(node_list, prompt_path=_prompt_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mcp_pool, _anthropic_chat, _prompt_path, _security_tools_config

    config = load_config()

    _prompt_path = config.get("prompt_path")
    _security_tools_config = config.get("security_tools", {})

    _mcp_pool = MCPPool()
    await _mcp_pool.connect_all(config.get("nodes", []))

    anthropic_cfg = config.get("anthropic", {})
    _anthropic_chat = AnthropicChat(
        model=anthropic_cfg.get("model", "claude-sonnet-4-20250514"),
        max_tokens=anthropic_cfg.get("max_tokens", 4096),
    )

    logger.info(
        f"Started with {len(_mcp_pool.nodes)} nodes, "
        f"{len(_build_tools())} tools"
    )
    yield

    await _mcp_pool.disconnect_all()


app = FastAPI(title="Statmon AI Aggregator", lifespan=lifespan)

import os

_static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
_template_dir = os.path.join(os.path.dirname(__file__), "templates")

if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

templates = Jinja2Templates(directory=_template_dir)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    configured = len(_mcp_pool.node_configs) if _mcp_pool else 0
    connected = len(_mcp_pool.nodes) if _mcp_pool else 0
    return templates.TemplateResponse(
        "chat.html",
        {
            "request": request,
            "configured": configured,
            "connected": connected,
        },
    )


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    message = body.get("message", "").strip()
    session_id = body.get("session_id")

    if not message:
        return JSONResponse(
            {"error": "Message is required"}, status_code=400
        )

    _evict_stale_sessions()

    if not session_id or session_id not in _sessions:
        session_id = str(uuid.uuid4())
        _sessions[session_id] = {"messages": [], "last_access": time.time()}

    session = _sessions[session_id]
    session["last_access"] = time.time()
    conversation = session["messages"]

    conversation.append({"role": "user", "content": message})

    tools = _build_tools()
    system_prompt = _build_system_prompt()

    trace = TraceCollector()
    try:
        response_text = await _anthropic_chat.run_turn(
            conversation, tools, _mcp_pool, system_prompt,
            trace=trace, security_tools_config=_security_tools_config,
        )
    except Exception:
        logger.exception("Error in conversation turn")
        conversation.pop()  # Remove the failed user message
        return JSONResponse(
            {"error": "An error occurred processing your request"},
            status_code=500,
        )

    return JSONResponse(
        {"response": response_text, "session_id": session_id, "trace": trace.to_dict()}
    )


@app.get("/api/nodes")
async def nodes():
    if _mcp_pool:
        await _mcp_pool.check_health()
        node_list = _mcp_pool.get_node_list()
        configured = len(_mcp_pool.node_configs)
        connected = sum(1 for n in node_list if n["status"] == "connected")
        return JSONResponse(
            {"nodes": node_list, "configured": configured, "connected": connected}
        )
    return JSONResponse({"nodes": [], "configured": 0, "connected": 0})


@app.get("/api/health")
async def health():
    node_count = len(_mcp_pool.nodes) if _mcp_pool else 0
    configured = len(_mcp_pool.node_configs) if _mcp_pool else 0
    tool_count = len(_build_tools())
    return JSONResponse(
        {
            "status": "ok",
            "nodes_connected": node_count,
            "nodes_configured": configured,
            "tools_available": tool_count,
        }
    )

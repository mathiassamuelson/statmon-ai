"""Logging filters to suppress noisy MCP client tracebacks.

Both the web app and CLI import this module to install the filters.
The MCP client library logs full tracebacks for transient SSE disconnects
that our reconnect logic handles transparently — these filters reduce
them to one-line warnings.
"""

import logging

logger = logging.getLogger(__name__)


class SSEDisconnectFilter(logging.Filter):
    """Downgrade noisy MCP SSE tracebacks to a short warning.

    The mcp library logs full ERROR tracebacks for both sse_reader and
    post_writer when a connection drops.  The reconnect logic in MCPPool
    handles recovery, so these are expected and harmless.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.funcName in ("sse_reader", "post_writer") and record.exc_info:
            exc = record.exc_info[1]
            msg = str(exc) if exc else record.getMessage()
            logger.warning(
                "MCP SSE connection issue in %s (%s) — "
                "will reconnect on next tool call",
                record.funcName,
                msg,
            )
            return False
        return True


def install():
    """Install all MCP log filters. Safe to call multiple times."""
    sse_logger = logging.getLogger("mcp.client.sse")
    # Avoid adding duplicate filters
    if not any(isinstance(f, SSEDisconnectFilter) for f in sse_logger.filters):
        sse_logger.addFilter(SSEDisconnectFilter())

"""Anthropic API conversation loop with tool call handling.

Implements the multi-turn conversation loop: call API -> handle tool_use
blocks -> call API again -> loop until no more tool calls.
"""

import asyncio
import json
import logging

import anthropic

from .mcp_pool import MCPPool
from .security_tools import SECURITY_TOOL_NAMES, dispatch as security_dispatch
from .trace import TraceCollector

from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 10
TRUNCATE_LIMIT = 15 * 1024  # 15KB

ProgressCallback = Callable[[dict], Awaitable[None]]


def _describe_tool_call(block) -> str:
    """Return a human-readable description of a tool call for progress display."""
    name = block.name
    inp = block.input if isinstance(block.input, dict) else {}

    # MCP tools: dns_node_a__statmon
    if "__" in name:
        node_part = name.rsplit("__", 1)[0].replace("_", "-")
        cmd = inp.get("command", "")
        return f"Querying {node_part}: {cmd}" if cmd else f"Querying {node_part}"

    # Security tools
    descs = {
        "whois_lookup": lambda: f"WHOIS lookup: {inp.get('domain', '')}",
        "dns_resolve": lambda: f"DNS resolve: {inp.get('name', '')}",
        "ip_geolocation": lambda: f"IP geolocation: {inp.get('ip', '')}",
        "reverse_dns_lookup": lambda: f"Reverse DNS: {inp.get('ip', '')}",
        "web_search": lambda: "Searching the web...",
    }
    if name in descs:
        return descs[name]()

    return f"Running {name}"


def _truncate(text: str) -> str:
    """Truncate tool result text to 15KB if needed."""
    if len(text) <= TRUNCATE_LIMIT:
        return text
    original_kb = len(text) / 1024
    return text[:TRUNCATE_LIMIT] + f"\n\n[truncated — original size: {original_kb:.1f}KB]"


class AnthropicChat:
    def __init__(self, model: str = "claude-sonnet-4-20250514", max_tokens: int = 4096):
        self.client = anthropic.AsyncAnthropic()
        self.model = model
        self.max_tokens = max_tokens

    async def run_turn(
        self,
        conversation: list[dict],
        tools: list[dict],
        mcp_pool: MCPPool,
        system_prompt: str,
        trace: TraceCollector | None = None,
        security_tools_config: dict | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> str:
        """Run one turn of conversation, handling tool calls in a loop.

        Args:
            conversation: The full conversation history (mutated in place).
            tools: Anthropic-format tool definitions.
            mcp_pool: The MCP pool for routing tool calls.
            system_prompt: The system prompt string.
            trace: Optional TraceCollector for timing instrumentation.
            security_tools_config: Optional config dict for security tools.
            on_progress: Optional async callback for streaming progress updates.

        Returns:
            The final assistant text response.
        """
        async def _progress(message: str):
            if on_progress:
                await on_progress({"type": "status", "message": message})

        for round_num in range(MAX_TOOL_ROUNDS):
            if trace:
                trace.start_round(round_num)

            if round_num == 0:
                await _progress("Thinking...")
            else:
                await _progress("Analyzing results...")

            if trace:
                with trace.span("anthropic_api", model=self.model) as api_span:
                    response = await self.client.messages.create(
                        model=self.model,
                        max_tokens=self.max_tokens,
                        system=system_prompt,
                        tools=tools,
                        messages=conversation,
                    )
                    api_span.metadata.update({
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "stop_reason": response.stop_reason,
                    })
                trace.record_api_call(api_span)
            else:
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system_prompt,
                    tools=tools,
                    messages=conversation,
                )

            if response.stop_reason != "tool_use":
                text_parts = [
                    block.text
                    for block in response.content
                    if block.type == "text"
                ]
                assistant_text = "\n".join(text_parts)
                conversation.append(
                    {"role": "assistant", "content": response.content}
                )
                return assistant_text

            # Handle tool calls — emit progress for each tool before executing
            conversation.append(
                {"role": "assistant", "content": response.content}
            )

            tool_use_blocks = [
                b for b in response.content if b.type == "tool_use"
            ]
            for block in tool_use_blocks:
                await _progress(_describe_tool_call(block))

            tool_results = await self._execute_tool_calls(
                response.content, mcp_pool, trace, security_tools_config
            )
            conversation.append({"role": "user", "content": tool_results})

        return "I've reached the maximum number of tool call rounds. Please try a more specific question."

    async def _execute_tool_calls(
        self,
        content_blocks,
        mcp_pool: MCPPool,
        trace: TraceCollector | None = None,
        security_tools_config: dict | None = None,
    ) -> list[dict]:
        """Execute all tool calls in a response, in parallel."""
        tool_use_blocks = [b for b in content_blocks if b.type == "tool_use"]

        async def _call_one(block):
            is_security = block.name in SECURITY_TOOL_NAMES
            inp = block.input if isinstance(block.input, dict) else {}
            # For trace: show command (MCP) or query (security tool)
            trace_query = inp.get("command", "") or inp.get("domain", "") or inp.get("name", "") or inp.get("ip", "")

            try:
                if is_security:
                    result_text = await self._call_security_tool(
                        block.name, inp, security_tools_config, trace, trace_query
                    )
                else:
                    result_text = await self._call_mcp_tool(
                        block.name, inp, mcp_pool, trace, trace_query
                    )
                return {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": _truncate(result_text),
                }
            except Exception as e:
                logger.exception(f"Tool call failed: {block.name}")
                return {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": f"Error executing tool: {e}",
                    "is_error": True,
                }

        if trace:
            with trace.span("tool_batch", tool_count=len(tool_use_blocks), parallel=True) as batch_span:
                results = await asyncio.gather(*[_call_one(b) for b in tool_use_blocks])
            trace.record_tool_batch(batch_span)
        else:
            results = await asyncio.gather(*[_call_one(b) for b in tool_use_blocks])
        return list(results)

    async def _call_mcp_tool(
        self, name: str, arguments: dict, mcp_pool: MCPPool,
        trace: TraceCollector | None, trace_query: str,
    ) -> str:
        """Execute an MCP tool call with optional tracing."""
        if trace:
            with trace.span("tool_call", tool_name=name, command=trace_query) as tc_span:
                result_text = await mcp_pool.call_tool(name, arguments)
                tc_span.metadata["response_bytes"] = len(result_text)
                try:
                    parsed = json.loads(result_text)
                    if "execution_time_ms" in parsed:
                        tc_span.metadata["cli_execution_ms"] = parsed["execution_time_ms"]
                    if "node" in parsed:
                        tc_span.metadata["node"] = parsed["node"]
                except (json.JSONDecodeError, TypeError):
                    pass
            trace.record_tool_call(tc_span)
        else:
            result_text = await mcp_pool.call_tool(name, arguments)
        return result_text

    async def _call_security_tool(
        self, name: str, arguments: dict, config: dict | None,
        trace: TraceCollector | None, trace_query: str,
    ) -> str:
        """Execute a local security tool call with optional tracing."""
        if trace:
            with trace.span("tool_call", tool_name=name, query=trace_query) as tc_span:
                result_text = await security_dispatch(name, arguments, config)
                tc_span.metadata["response_bytes"] = len(result_text)
            trace.record_tool_call(tc_span)
        else:
            result_text = await security_dispatch(name, arguments, config)
        return result_text

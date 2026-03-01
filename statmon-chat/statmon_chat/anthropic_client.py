"""Anthropic API conversation loop with tool call handling.

Implements the multi-turn conversation loop: call API -> handle tool_use
blocks -> call API again -> loop until no more tool calls.
"""

import asyncio
import logging

import anthropic

from .mcp_pool import MCPPool

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 10
TRUNCATE_LIMIT = 15 * 1024  # 15KB


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
    ) -> str:
        """Run one turn of conversation, handling tool calls in a loop.

        Args:
            conversation: The full conversation history (mutated in place).
            tools: Anthropic-format tool definitions.
            mcp_pool: The MCP pool for routing tool calls.
            system_prompt: The system prompt string.

        Returns:
            The final assistant text response.
        """
        for round_num in range(MAX_TOOL_ROUNDS):
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

            # Handle tool calls
            conversation.append(
                {"role": "assistant", "content": response.content}
            )

            tool_results = await self._execute_tool_calls(
                response.content, mcp_pool
            )
            conversation.append({"role": "user", "content": tool_results})

        return "I've reached the maximum number of tool call rounds. Please try a more specific question."

    async def _execute_tool_calls(
        self, content_blocks, mcp_pool: MCPPool
    ) -> list[dict]:
        """Execute all tool calls in a response, in parallel."""
        tool_use_blocks = [b for b in content_blocks if b.type == "tool_use"]

        async def _call_one(block):
            try:
                result_text = await mcp_pool.call_tool(
                    block.name, block.input
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

        results = await asyncio.gather(*[_call_one(b) for b in tool_use_blocks])
        return list(results)

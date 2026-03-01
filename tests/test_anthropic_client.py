"""Tests for statmon_chat.anthropic_client — conversation loop."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from statmon_chat.anthropic_client import AnthropicChat, _truncate


class TestTruncate:
    def test_short_text_unchanged(self):
        text = "short"
        assert _truncate(text) == text

    def test_long_text_truncated(self):
        text = "x" * 20000
        result = _truncate(text)
        assert len(result) < len(text)
        assert "[truncated" in result

    def test_exactly_at_limit(self):
        text = "x" * (15 * 1024)
        assert _truncate(text) == text


class TestAnthropicChat:
    @pytest.mark.asyncio
    async def test_simple_text_response(self):
        chat = AnthropicChat()

        mock_response = SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="Hello!")],
        )

        with patch.object(
            chat.client.messages, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            conversation = [{"role": "user", "content": "Hi"}]
            result = await chat.run_turn(
                conversation, [], MagicMock(), "system prompt"
            )

            assert result == "Hello!"
            assert len(conversation) == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_tool_call_then_text(self):
        chat = AnthropicChat()

        tool_use_block = SimpleNamespace(
            type="tool_use",
            id="tool_1",
            name="dns_node_a__statmon",
            input={"command": "querystore.count"},
        )
        tool_response = SimpleNamespace(
            stop_reason="tool_use",
            content=[tool_use_block],
        )
        final_response = SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="The count is 42.")],
        )

        mock_pool = MagicMock()
        mock_pool.call_tool = AsyncMock(return_value='{"count": 42}')

        with patch.object(
            chat.client.messages, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.side_effect = [tool_response, final_response]

            conversation = [{"role": "user", "content": "count queries"}]
            result = await chat.run_turn(
                conversation, [], mock_pool, "system prompt"
            )

            assert result == "The count is 42."
            mock_pool.call_tool.assert_called_once_with(
                "dns_node_a__statmon", {"command": "querystore.count"}
            )
            # user + assistant(tool_use) + user(tool_result) + assistant(text)
            assert len(conversation) == 4

    @pytest.mark.asyncio
    async def test_tool_call_error_handled(self):
        chat = AnthropicChat()

        tool_use_block = SimpleNamespace(
            type="tool_use",
            id="tool_1",
            name="dns_node_a__statmon",
            input={"command": "querystore.count"},
        )
        tool_response = SimpleNamespace(
            stop_reason="tool_use",
            content=[tool_use_block],
        )
        final_response = SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="Tool failed, sorry.")],
        )

        mock_pool = MagicMock()
        mock_pool.call_tool = AsyncMock(side_effect=Exception("Connection lost"))

        with patch.object(
            chat.client.messages, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.side_effect = [tool_response, final_response]

            conversation = [{"role": "user", "content": "count queries"}]
            result = await chat.run_turn(
                conversation, [], mock_pool, "system prompt"
            )

            assert result == "Tool failed, sorry."

    @pytest.mark.asyncio
    async def test_max_rounds_guard(self):
        chat = AnthropicChat()

        tool_use_block = SimpleNamespace(
            type="tool_use",
            id="tool_1",
            name="dns_node_a__statmon",
            input={"command": "querystore.count"},
        )
        looping_response = SimpleNamespace(
            stop_reason="tool_use",
            content=[tool_use_block],
        )

        mock_pool = MagicMock()
        mock_pool.call_tool = AsyncMock(return_value='{"count": 42}')

        with patch.object(
            chat.client.messages, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = looping_response

            conversation = [{"role": "user", "content": "loop forever"}]
            result = await chat.run_turn(
                conversation, [], mock_pool, "system prompt"
            )

            assert "maximum number of tool call rounds" in result

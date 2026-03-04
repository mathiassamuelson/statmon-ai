"""Tests for statmon_chat.anthropic_client — conversation loop."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from statmon_chat.anthropic_client import AnthropicChat, _truncate
from statmon_chat.trace import TraceCollector


def _mock_usage(input_tokens=100, output_tokens=50):
    return SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)


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
            usage=_mock_usage(),
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
    async def test_simple_text_response_with_trace(self):
        chat = AnthropicChat()
        trace = TraceCollector()

        mock_response = SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="Hello!")],
            usage=_mock_usage(200, 80),
        )

        with patch.object(
            chat.client.messages, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_response

            conversation = [{"role": "user", "content": "Hi"}]
            result = await chat.run_turn(
                conversation, [], MagicMock(), "system prompt", trace=trace
            )

            assert result == "Hello!"
            assert len(trace.rounds) == 1
            assert trace.rounds[0].api_call is not None
            assert trace.rounds[0].api_call.metadata["input_tokens"] == 200
            assert trace.rounds[0].api_call.metadata["output_tokens"] == 80
            assert trace.rounds[0].api_call.metadata["stop_reason"] == "end_turn"

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
            usage=_mock_usage(),
        )
        final_response = SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="The count is 42.")],
            usage=_mock_usage(),
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
    async def test_tool_call_with_trace(self):
        chat = AnthropicChat()
        trace = TraceCollector()

        tool_use_block = SimpleNamespace(
            type="tool_use",
            id="tool_1",
            name="dns_node_a__statmon",
            input={"command": "querystore.top-clients duration=3600"},
        )
        tool_response = SimpleNamespace(
            stop_reason="tool_use",
            content=[tool_use_block],
            usage=_mock_usage(500, 100),
        )
        final_response = SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="Done.")],
            usage=_mock_usage(800, 200),
        )

        mock_pool = MagicMock()
        mock_pool.call_tool = AsyncMock(
            return_value='{"execution_time_ms": 350, "node": "dns-node-a", "result": []}'
        )

        with patch.object(
            chat.client.messages, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_create.side_effect = [tool_response, final_response]

            conversation = [{"role": "user", "content": "top clients"}]
            result = await chat.run_turn(
                conversation, [], mock_pool, "system prompt", trace=trace
            )

            assert result == "Done."
            assert len(trace.rounds) == 2

            # Round 0: API call + tool batch + tool call
            r0 = trace.rounds[0]
            assert r0.api_call.metadata["input_tokens"] == 500
            assert r0.tool_batch is not None
            assert len(r0.tool_calls) == 1
            tc = r0.tool_calls[0]
            assert tc.metadata["tool_name"] == "dns_node_a__statmon"
            assert tc.metadata["command"] == "querystore.top-clients duration=3600"
            assert tc.metadata["cli_execution_ms"] == 350
            assert tc.metadata["node"] == "dns-node-a"

            # Round 1: API call only
            r1 = trace.rounds[1]
            assert r1.api_call.metadata["stop_reason"] == "end_turn"

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
            usage=_mock_usage(),
        )
        final_response = SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="Tool failed, sorry.")],
            usage=_mock_usage(),
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
            usage=_mock_usage(),
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

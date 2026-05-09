"""Tests for copilot.cli — input parsing and serialization."""

import json
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from copilot.cli import (
    parse_input_file,
    serialize_conversation,
    run_conversations,
)


class TestParseInputFile:
    def test_single_conversation_single_turn(self, tmp_path):
        f = tmp_path / "prompts.txt"
        f.write_text("What is the QPS?\n")
        result = parse_input_file(str(f))
        assert result == [["What is the QPS?"]]

    def test_single_conversation_multi_turn(self, tmp_path):
        f = tmp_path / "prompts.txt"
        f.write_text("What is the QPS?\nWhich node is highest?\n")
        result = parse_input_file(str(f))
        assert result == [["What is the QPS?", "Which node is highest?"]]

    def test_multiple_conversations(self, tmp_path):
        f = tmp_path / "prompts.txt"
        f.write_text(
            "Turn 1a\nTurn 1b\n\nTurn 2a\nTurn 2b\nTurn 2c\n"
        )
        result = parse_input_file(str(f))
        assert result == [
            ["Turn 1a", "Turn 1b"],
            ["Turn 2a", "Turn 2b", "Turn 2c"],
        ]

    def test_extra_blank_lines_ignored(self, tmp_path):
        f = tmp_path / "prompts.txt"
        f.write_text("\n\nTurn 1\n\n\n\nTurn 2\n\n")
        result = parse_input_file(str(f))
        assert result == [["Turn 1"], ["Turn 2"]]

    def test_empty_file(self, tmp_path):
        f = tmp_path / "prompts.txt"
        f.write_text("")
        result = parse_input_file(str(f))
        assert result == []

    def test_comment_lines_ignored(self, tmp_path):
        f = tmp_path / "prompts.txt"
        f.write_text(
            "# Scenario 1: Health check\n"
            "Show me QPS\n"
            "What about SERVFAIL?\n"
            "\n"
            "# Scenario 2: DDoS\n"
            "Show me top clients\n"
        )
        result = parse_input_file(str(f))
        assert result == [
            ["Show me QPS", "What about SERVFAIL?"],
            ["Show me top clients"],
        ]

    def test_whitespace_only_lines_ignored(self, tmp_path):
        f = tmp_path / "prompts.txt"
        f.write_text("Turn 1\n   \nTurn 2\n")
        result = parse_input_file(str(f))
        assert result == [["Turn 1"], ["Turn 2"]]


class TestSerializeConversation:
    def test_plain_user_message(self):
        conv = [{"role": "user", "content": "Hello"}]
        result = serialize_conversation(conv)
        assert result == [{"role": "user", "content": "Hello"}]

    def test_pydantic_assistant_blocks(self):
        """Pydantic objects with model_dump() are serialized."""
        block = MagicMock()
        block.model_dump.return_value = {
            "type": "text", "text": "Hi!"
        }
        conv = [{"role": "assistant", "content": [block]}]
        result = serialize_conversation(conv)
        assert result == [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Hi!"}
            ]}
        ]
        block.model_dump.assert_called_once()

    def test_dict_assistant_blocks_pass_through(self):
        """Plain dicts in assistant content are kept as-is."""
        block = {"type": "text", "text": "Hi!"}
        conv = [{"role": "assistant", "content": [block]}]
        result = serialize_conversation(conv)
        assert result == [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Hi!"}
            ]}
        ]

    def test_tool_result_user_message(self):
        """Tool result messages (list content) pass through."""
        tool_result = [
            {
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": '{"count": 42}',
            }
        ]
        conv = [{"role": "user", "content": tool_result}]
        result = serialize_conversation(conv)
        assert result == [{"role": "user", "content": tool_result}]

    def test_full_conversation_round_trip(self):
        """A multi-turn conversation serializes correctly."""
        text_block = MagicMock()
        text_block.model_dump.return_value = {
            "type": "text", "text": "Let me check."
        }
        tool_block = MagicMock()
        tool_block.model_dump.return_value = {
            "type": "tool_use",
            "id": "t1",
            "name": "dns_node_a__statmon",
            "input": {"command": "qps"},
        }
        final_block = MagicMock()
        final_block.model_dump.return_value = {
            "type": "text", "text": "QPS is 1234."
        }

        conv = [
            {"role": "user", "content": "What is the QPS?"},
            {"role": "assistant", "content": [text_block, tool_block]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": '{"qps": 1234}',
                    }
                ],
            },
            {"role": "assistant", "content": [final_block]},
        ]
        result = serialize_conversation(conv)

        # Should be fully JSON-serializable
        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        assert len(parsed) == 4
        assert parsed[0]["role"] == "user"
        assert parsed[1]["content"][1]["name"] == "dns_node_a__statmon"
        assert parsed[3]["content"][0]["text"] == "QPS is 1234."


class TestRunConversations:
    @pytest.mark.asyncio
    async def test_writes_jsonl_output(self, tmp_path):
        """Runs conversations and writes valid JSONL."""
        output_file = str(tmp_path / "output.jsonl")
        conversations = [["Hello"], ["Hi", "Follow up"]]

        mock_pool = MagicMock()
        mock_pool.nodes = {"dns-node-a": MagicMock()}
        mock_pool.connect_all = AsyncMock()
        mock_pool.disconnect_all = AsyncMock()
        mock_pool.build_anthropic_tools.return_value = []
        mock_pool.get_node_list.return_value = [
            {"name": "dns-node-a", "status": "connected"}
        ]

        mock_chat = MagicMock()
        mock_chat.model = "test-model"

        async def mock_run_turn(
            conversation, tools, mcp_pool, system_prompt, **kwargs
        ):
            # Simulate real run_turn: append assistant message
            conversation.append(
                {"role": "assistant", "content": "Mock response"}
            )
            return "Mock response"

        mock_chat.run_turn = AsyncMock(side_effect=mock_run_turn)

        config = {
            "nodes": [],
            "anthropic": {"model": "test-model"},
        }

        with patch("copilot.cli.MCPPool", return_value=mock_pool):
            with patch(
                "copilot.cli.AnthropicChat",
                return_value=mock_chat,
            ):
                with patch(
                    "copilot.cli.build_system_prompt",
                    return_value="test prompt",
                ):
                    await run_conversations(
                        conversations, output_file, config
                    )

        with open(output_file) as f:
            lines = f.readlines()

        assert len(lines) == 2

        record0 = json.loads(lines[0])
        assert record0["system_prompt"] == "test prompt"
        assert record0["metadata"]["turn_count"] == 1
        assert record0["metadata"]["completed_turns"] == 1
        assert len(record0["messages"]) == 2  # user + assistant

        record1 = json.loads(lines[1])
        assert record1["metadata"]["turn_count"] == 2
        assert record1["metadata"]["completed_turns"] == 2
        assert len(record1["messages"]) == 4  # 2x (user + assistant)

        # Conversations should be independent
        assert mock_chat.run_turn.call_count == 3
        mock_pool.disconnect_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_resume_skips_completed(self, tmp_path):
        """Resume appends to existing file, skipping done conversations."""
        output_file = str(tmp_path / "output.jsonl")
        conversations = [["First"], ["Second"], ["Third"]]

        # Pre-populate with 1 completed conversation
        with open(output_file, "w") as f:
            f.write(json.dumps({"messages": [], "metadata": {}}) + "\n")

        mock_pool = MagicMock()
        mock_pool.nodes = {"dns-node-a": MagicMock()}
        mock_pool.connect_all = AsyncMock()
        mock_pool.disconnect_all = AsyncMock()
        mock_pool.build_anthropic_tools.return_value = []
        mock_pool.get_node_list.return_value = [
            {"name": "dns-node-a", "status": "connected"}
        ]

        mock_chat = MagicMock()
        mock_chat.model = "test-model"

        async def mock_run_turn(
            conversation, tools, mcp_pool, system_prompt, **kwargs
        ):
            conversation.append(
                {"role": "assistant", "content": "Mock response"}
            )
            return "Mock response"

        mock_chat.run_turn = AsyncMock(side_effect=mock_run_turn)

        config = {
            "nodes": [],
            "anthropic": {"model": "test-model"},
        }

        with patch("copilot.cli.MCPPool", return_value=mock_pool):
            with patch(
                "copilot.cli.AnthropicChat",
                return_value=mock_chat,
            ):
                with patch(
                    "copilot.cli.build_system_prompt",
                    return_value="test prompt",
                ):
                    await run_conversations(
                        conversations,
                        output_file,
                        config,
                        resume=True,
                    )

        with open(output_file) as f:
            lines = f.readlines()

        # 1 pre-existing + 2 new
        assert len(lines) == 3
        # Only ran 2 conversations (skipped first)
        assert mock_chat.run_turn.call_count == 2

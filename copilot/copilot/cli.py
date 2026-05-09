"""CLI tool for driving automated conversations to produce LoRA training data.

Reads a prompt file, runs multi-turn conversations against live MCP nodes,
and writes JSONL output with complete conversation histories including
tool calls and results.

Input file format:
  - Single newline separates turns within a conversation
  - Blank line (double newline) separates conversations

Usage:
  copilot-cli prompts.txt -o output.jsonl
  copilot-cli prompts.txt -o output.jsonl -c /path/to/config.yaml
"""

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import log_filters
from .anthropic_client import AnthropicChat
from .app import load_config
from .mcp_pool import MCPPool
from .security_tools import get_tool_definitions as get_security_tools
from .system_prompt import build_system_prompt
from .trace import TraceCollector

logger = logging.getLogger(__name__)


def parse_input_file(path: str) -> list[list[str]]:
    """Parse a prompt file into conversations.

    Returns a list of conversations, where each conversation is a list
    of prompt strings (one per turn).
    """
    text = Path(path).read_text()
    raw_conversations = re.split(r"\n\s*\n", text.strip())
    conversations = []
    for block in raw_conversations:
        prompts = [
            line.strip()
            for line in block.strip().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if prompts:
            conversations.append(prompts)
    return conversations


def serialize_conversation(conversation: list[dict]) -> list[dict]:
    """Convert conversation to JSON-serializable form.

    Assistant content blocks are Anthropic SDK Pydantic objects;
    call model_dump() on each to get plain dicts.
    """
    result = []
    for msg in conversation:
        role = msg["role"]
        content = msg["content"]
        if role == "assistant" and isinstance(content, list):
            serialized_content = []
            for block in content:
                if hasattr(block, "model_dump"):
                    serialized_content.append(block.model_dump())
                else:
                    serialized_content.append(block)
            result.append({"role": role, "content": serialized_content})
        else:
            result.append({"role": role, "content": content})
    return result


def _count_existing_lines(path: str) -> int:
    """Count non-empty lines in an existing file, or 0 if missing."""
    try:
        with open(path) as f:
            return sum(1 for line in f if line.strip())
    except FileNotFoundError:
        return 0


async def run_conversations(
    conversations: list[list[str]],
    output_file: str,
    config: dict,
    delay: float = 0,
    resume: bool = False,
) -> None:
    """Run all conversations and write JSONL output."""
    mcp_pool = MCPPool()
    await mcp_pool.connect_all(config.get("nodes", []))

    anthropic_cfg = config.get("anthropic", {})
    chat = AnthropicChat(
        model=anthropic_cfg.get("model", "claude-sonnet-4-20250514"),
        max_tokens=anthropic_cfg.get("max_tokens", 4096),
    )

    prompt_path = config.get("prompt_path")
    security_tools_config = config.get("security_tools", {})

    mcp_tools = mcp_pool.build_anthropic_tools()
    security_tools = get_security_tools()
    tools = (
        mcp_tools
        + security_tools
        + [{"type": "web_search_20250305", "name": "web_search"}]
    )

    node_list = mcp_pool.get_node_list()
    system_prompt = build_system_prompt(
        node_list, prompt_path=prompt_path
    )

    total_convos = len(conversations)
    skip = 0
    if resume:
        skip = _count_existing_lines(output_file)
        if skip >= total_convos:
            print(
                f"All {total_convos} conversation(s) already complete "
                f"in {output_file}",
                file=sys.stderr,
            )
            await mcp_pool.disconnect_all()
            return
        if skip > 0:
            print(
                f"Resuming: skipping {skip} completed conversation(s)",
                file=sys.stderr,
            )

    print(
        f"Loaded {total_convos} conversation(s) "
        f"({total_convos - skip} remaining), "
        f"{len(mcp_pool.nodes)} node(s) connected, "
        f"{len(tools)} tools available",
        file=sys.stderr,
    )

    try:
        mode = "a" if resume and skip > 0 else "w"
        with open(output_file, mode) as out:
            for conv_idx, prompts in enumerate(conversations, 1):
                if conv_idx <= skip:
                    continue

                if delay > 0 and conv_idx > skip + 1:
                    print(
                        f"  Waiting {delay}s...",
                        file=sys.stderr,
                    )
                    await asyncio.sleep(delay)

                print(
                    f"\n--- Conversation {conv_idx}/{total_convos} "
                    f"({len(prompts)} turn(s)) ---",
                    file=sys.stderr,
                )

                conversation: list[dict] = []
                turn_traces = []

                for turn_idx, prompt in enumerate(prompts, 1):
                    label = prompt[:80]
                    if len(prompt) > 80:
                        label += "..."
                    print(
                        f"  Turn {turn_idx}: {label}",
                        file=sys.stderr,
                    )

                    conversation.append(
                        {"role": "user", "content": prompt}
                    )
                    trace = TraceCollector()

                    try:
                        response_text = await chat.run_turn(
                            conversation,
                            tools,
                            mcp_pool,
                            system_prompt,
                            trace=trace,
                            security_tools_config=security_tools_config,
                        )
                        trace_dict = trace.to_dict()
                        turn_traces.append(trace_dict)
                        print(
                            f"    -> {len(response_text)} chars, "
                            f"{trace_dict['tool_call_count']} tool call(s)",
                            file=sys.stderr,
                        )
                    except Exception as e:
                        logger.exception("Turn failed")
                        print(
                            f"    ERROR: {e}",
                            file=sys.stderr,
                        )
                        conversation.pop()
                        turn_traces.append({"error": str(e)})
                        break

                record = {
                    "system_prompt": system_prompt,
                    "tools": tools,
                    "messages": serialize_conversation(conversation),
                    "traces": turn_traces,
                    "metadata": {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "conversation_index": conv_idx - 1,
                        "turn_count": len(prompts),
                        "completed_turns": len(
                            [t for t in turn_traces if "error" not in t]
                        ),
                        "model": chat.model,
                        "nodes": [n["name"] for n in node_list],
                    },
                }
                out.write(json.dumps(record) + "\n")
                out.flush()
    finally:
        await mcp_pool.disconnect_all()

    wrote = total_convos - skip
    print(
        f"\nDone. Wrote {wrote} conversation(s) to {output_file}"
        f" ({total_convos} total)",
        file=sys.stderr,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Drive automated conversations for LoRA "
            "training data generation"
        ),
        prog="copilot-cli",
    )
    parser.add_argument("input_file", help="Path to prompt file")
    parser.add_argument(
        "-o", "--output", required=True, help="Output JSONL file path"
    )
    parser.add_argument(
        "-c",
        "--config",
        default=None,
        help="Config file path (overrides COPILOT_CONFIG)",
    )
    parser.add_argument(
        "-d",
        "--delay",
        type=float,
        default=2.0,
        help="Seconds to wait between conversations (default: 2)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from where a previous run left off",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    log_filters.install()

    if args.config:
        import os

        os.environ["COPILOT_CONFIG"] = args.config

    conversations = parse_input_file(args.input_file)
    if not conversations:
        print("No conversations found in input file", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    asyncio.run(
        run_conversations(
            conversations,
            args.output,
            config,
            delay=args.delay,
            resume=args.resume,
        )
    )


if __name__ == "__main__":
    main()

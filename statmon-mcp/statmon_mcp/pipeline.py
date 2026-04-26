"""Sandboxed pipeline grammar for catalog tools.

A command string is a sequence of segments separated by literal `|`
characters that appear *outside* any quoted string. The lead segment is
the lead tool's args; non-lead segments start with the tool name. Only
catalog tools with `pipe_stage: true` may appear in non-lead position.

Forbidden unquoted metacharacters reject the whole pipeline: ; & > <
` $( && || newline. Globs (* ? […]) are passed verbatim. No shell.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .catalog import ToolEntry, ToolRegistry


class PipelineGrammarError(Exception):
    """Raised when the command string is not a legal pipeline."""


class PipelineResolutionError(Exception):
    """Raised when a referenced tool is missing or used in non-lead position
    without pipe_stage=True."""


_FORBIDDEN_TOKENS = (";", "&", ">", "<", "`", "$(", "&&", "||", "\n")


def _scan_segments(command: str) -> list[str]:
    """Split on unquoted `|`. Detects forbidden metacharacters along the way."""
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    n = len(command)
    while i < n:
        c = command[i]

        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            elif c == "\\" and i + 1 < n:
                buf.append(command[i + 1])
                i += 2
                continue
            i += 1
            continue

        if c in ("'", '"'):
            quote = c
            buf.append(c)
            i += 1
            continue

        # Forbidden double-char tokens first.
        two = command[i:i + 2]
        if two in ("&&", "||", "$("):
            raise PipelineGrammarError(
                f"pipeline grammar: forbidden metacharacter {two!r}"
            )

        if c == "|":
            segments.append("".join(buf))
            buf = []
            i += 1
            continue

        if c in _FORBIDDEN_TOKENS:
            raise PipelineGrammarError(
                f"pipeline grammar: forbidden metacharacter {c!r}"
            )

        buf.append(c)
        i += 1

    if quote:
        raise PipelineGrammarError(f"pipeline grammar: unterminated {quote!r} quote")

    segments.append("".join(buf))
    return segments


def parse_pipeline(command: str) -> list[str]:
    """Parse a command string into a list of segment strings.

    Lead segment is segments[0]; subsequent segments are non-lead and start
    with `<tool-name> <args...>`.

    Raises PipelineGrammarError on forbidden metacharacters, empty segments,
    or unterminated quotes.
    """
    segments = _scan_segments(command)
    cleaned = [s.strip() for s in segments]
    if any(not s for s in cleaned):
        raise PipelineGrammarError("pipeline grammar: empty segment")
    return cleaned


def resolve_pipeline(
    lead_entry: "ToolEntry",
    segments: list[str],
    registry: "ToolRegistry",
) -> list[tuple["ToolEntry", str]]:
    """Map parsed segments to (ToolEntry, args) pairs.

    The first segment is bound to lead_entry as-is (its text is the args).
    Each subsequent segment's first shlex token is interpreted as a catalog
    tool name and must be pipe_stage=True.
    """
    if not segments:
        raise PipelineResolutionError("empty pipeline")

    stages: list[tuple["ToolEntry", str]] = [(lead_entry, segments[0])]

    for i, seg in enumerate(segments[1:], start=1):
        try:
            tokens = shlex.split(seg)
        except ValueError as e:
            raise PipelineResolutionError(f"segment {i}: {e}")
        if not tokens:
            raise PipelineResolutionError(f"segment {i}: empty")
        tool_name = tokens[0]
        entry = registry.get(tool_name)
        if entry is None:
            raise PipelineResolutionError(
                f"segment {i}: unknown tool {tool_name!r}"
            )
        if not entry.pipe_stage:
            raise PipelineResolutionError(
                f"segment {i}: {tool_name!r} is not a pipe stage"
            )
        # Re-extract the original args text after the tool name. shlex.join
        # would re-quote; we just take everything after the name.
        rest = seg[len(tool_name):].lstrip()
        stages.append((entry, rest))

    return stages

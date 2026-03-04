"""Request-level tracing for observability.

Records timing spans for Anthropic API calls, MCP tool calls, and CLI
execution so each /api/chat response includes a breakdown of where
time was spent.
"""

import time
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class Span:
    name: str
    start_ms: float
    duration_ms: float = 0.0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {"name": self.name, "start_ms": round(self.start_ms, 1), "duration_ms": round(self.duration_ms, 1)}
        if self.metadata:
            d["metadata"] = self.metadata
        return d


@dataclass
class RoundTrace:
    round_num: int
    api_call: Span | None = None
    tool_batch: Span | None = None
    tool_calls: list[Span] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {"round_num": self.round_num}
        if self.api_call:
            d["api_call"] = self.api_call.to_dict()
        if self.tool_batch:
            d["tool_batch"] = self.tool_batch.to_dict()
        if self.tool_calls:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        return d


@dataclass
class TurnTrace:
    total_ms: float = 0.0
    rounds: list[RoundTrace] = field(default_factory=list)

    def to_dict(self) -> dict:
        tool_call_count = sum(len(r.tool_calls) for r in self.rounds)
        return {
            "total_ms": round(self.total_ms, 1),
            "round_count": len(self.rounds),
            "tool_call_count": tool_call_count,
            "rounds": [r.to_dict() for r in self.rounds],
        }


class TraceCollector:
    """Collects timing spans relative to a request start time."""

    def __init__(self):
        self._start = time.monotonic()
        self.rounds: list[RoundTrace] = []
        self._current_round: RoundTrace | None = None

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self._start) * 1000

    def start_round(self, round_num: int) -> None:
        self._current_round = RoundTrace(round_num=round_num)
        self.rounds.append(self._current_round)

    @contextmanager
    def span(self, name: str, **metadata):
        """Context manager that yields a Span, timing the block."""
        s = Span(name=name, start_ms=self.elapsed_ms(), metadata=metadata)
        try:
            yield s
        finally:
            s.duration_ms = self.elapsed_ms() - s.start_ms

    def record_api_call(self, span: Span) -> None:
        if self._current_round:
            self._current_round.api_call = span

    def record_tool_batch(self, span: Span) -> None:
        if self._current_round:
            self._current_round.tool_batch = span

    def record_tool_call(self, span: Span) -> None:
        if self._current_round:
            self._current_round.tool_calls.append(span)

    def to_dict(self) -> dict:
        total_ms = self.elapsed_ms()
        trace = TurnTrace(total_ms=total_ms, rounds=self.rounds)
        return trace.to_dict()

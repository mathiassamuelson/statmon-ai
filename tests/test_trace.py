"""Tests for copilot.trace — timing instrumentation."""

import time

from copilot.trace import TraceCollector, Span, RoundTrace, TurnTrace


class TestSpan:
    def test_to_dict_minimal(self):
        s = Span(name="test", start_ms=0.0, duration_ms=100.0)
        d = s.to_dict()
        assert d == {"name": "test", "start_ms": 0.0, "duration_ms": 100.0}

    def test_to_dict_with_metadata(self):
        s = Span(name="api", start_ms=10.0, duration_ms=200.0, metadata={"model": "claude"})
        d = s.to_dict()
        assert d["metadata"] == {"model": "claude"}


class TestRoundTrace:
    def test_to_dict_api_only(self):
        api = Span(name="anthropic_api", start_ms=0.0, duration_ms=500.0)
        r = RoundTrace(round_num=0, api_call=api)
        d = r.to_dict()
        assert d["round_num"] == 0
        assert d["api_call"]["duration_ms"] == 500.0
        assert "tool_batch" not in d
        assert "tool_calls" not in d

    def test_to_dict_with_tools(self):
        api = Span(name="anthropic_api", start_ms=0.0, duration_ms=500.0)
        batch = Span(name="tool_batch", start_ms=500.0, duration_ms=300.0)
        tc = Span(name="tool_call", start_ms=500.0, duration_ms=250.0, metadata={"tool_name": "statmon"})
        r = RoundTrace(round_num=1, api_call=api, tool_batch=batch, tool_calls=[tc])
        d = r.to_dict()
        assert len(d["tool_calls"]) == 1
        assert d["tool_batch"]["duration_ms"] == 300.0


class TestTurnTrace:
    def test_to_dict_counts(self):
        tc1 = Span(name="tool_call", start_ms=0.0, duration_ms=100.0)
        tc2 = Span(name="tool_call", start_ms=0.0, duration_ms=200.0)
        r1 = RoundTrace(round_num=0, tool_calls=[tc1, tc2])
        r2 = RoundTrace(round_num=1, tool_calls=[])
        t = TurnTrace(total_ms=1000.0, rounds=[r1, r2])
        d = t.to_dict()
        assert d["total_ms"] == 1000.0
        assert d["round_count"] == 2
        assert d["tool_call_count"] == 2


class TestTraceCollector:
    def test_elapsed_ms(self):
        tc = TraceCollector()
        time.sleep(0.01)
        assert tc.elapsed_ms() >= 10

    def test_span_context_manager(self):
        tc = TraceCollector()
        with tc.span("test", key="value") as s:
            time.sleep(0.01)
        assert s.name == "test"
        assert s.duration_ms >= 10
        assert s.metadata["key"] == "value"

    def test_round_recording(self):
        tc = TraceCollector()
        tc.start_round(0)

        api_span = Span(name="anthropic_api", start_ms=0.0, duration_ms=100.0)
        tc.record_api_call(api_span)

        batch_span = Span(name="tool_batch", start_ms=100.0, duration_ms=50.0)
        tc.record_tool_batch(batch_span)

        tool_span = Span(name="tool_call", start_ms=100.0, duration_ms=40.0)
        tc.record_tool_call(tool_span)

        assert len(tc.rounds) == 1
        assert tc.rounds[0].api_call is api_span
        assert tc.rounds[0].tool_batch is batch_span
        assert tc.rounds[0].tool_calls == [tool_span]

    def test_to_dict(self):
        tc = TraceCollector()
        tc.start_round(0)
        d = tc.to_dict()
        assert "total_ms" in d
        assert d["round_count"] == 1
        assert d["tool_call_count"] == 0

    def test_no_round_recording_ignored(self):
        tc = TraceCollector()
        # No round started — record calls should not raise
        tc.record_api_call(Span(name="api", start_ms=0.0))
        tc.record_tool_batch(Span(name="batch", start_ms=0.0))
        tc.record_tool_call(Span(name="tool", start_ms=0.0))
        assert len(tc.rounds) == 0

# Observability Plan

Add timing instrumentation to the chat app so that every `/api/chat` response includes a breakdown of where time was spent. The goal is visibility, not optimization — understand the cost of each step before deciding what to improve.

## What gets measured

For each chat request, collect a timeline of spans:

| Span | Where | What it captures |
|---|---|---|
| `total` | `app.py` chat endpoint | Wall-clock time from request received to response sent |
| `anthropic_api` | `anthropic_client.py` run_turn loop | Each `messages.create()` call — duration, model, input/output token counts, stop_reason |
| `tool_execution` | `anthropic_client.py` _execute_tool_calls | Wall-clock of the entire `asyncio.gather()` — how long the parallel batch took |
| `tool_call` | `mcp_pool.py` call_tool | Each individual MCP tool call — tool name, node, duration, response size |
| `cli_execution` | Already exists in cli_executor.py | `execution_time_ms` is already returned in MCP responses — surface it |

Each API round in the tool loop produces a "round" entry so multi-round conversations are visible (e.g., round 1: API call 2.3s → 3 tool calls max 1.1s → round 2: API call 1.8s → done).

## Data model

```python
@dataclass
class Span:
    name: str               # e.g. "anthropic_api", "tool_call"
    start_ms: float         # relative to request start
    duration_ms: float
    metadata: dict          # varies by span type

@dataclass
class TurnTrace:
    total_ms: float
    rounds: list[RoundTrace]

@dataclass
class RoundTrace:
    round_num: int
    api_call: Span          # anthropic API span
    tool_batch: Span | None # the gather() span, if tools were called
    tool_calls: list[Span]  # individual tool call spans within the batch
```

`Span.metadata` contents by span type:

- **anthropic_api**: `model`, `input_tokens`, `output_tokens`, `stop_reason`
- **tool_call**: `tool_name`, `node`, `command` (the querystore command sent, e.g. `querystore.top-clients duration=3600`), `response_bytes`, `cli_execution_ms` (from MCP response)
- **tool_batch**: `tool_count`, `parallel` (always true for now)

## Implementation

### 1. Add `trace.py` module

New file: `statmon-chat/statmon_chat/trace.py`

Contains the `Span`, `RoundTrace`, `TurnTrace` dataclasses and a `TraceCollector` context manager that records spans relative to request start. Simple `time.monotonic()` based — no external dependencies.

```python
class TraceCollector:
    def __init__(self):
        self._start = time.monotonic()
        self.rounds: list[RoundTrace] = []

    def elapsed_ms(self) -> float:
        """Milliseconds since collector was created."""
        return (time.monotonic() - self._start) * 1000

    def span(self, name: str, **metadata) -> SpanContext:
        """Context manager that times a block and returns a Span."""
        ...

    def to_dict(self) -> dict:
        """Serialize the full trace for JSON response."""
        ...
```

### 2. Instrument `anthropic_client.py`

Pass a `TraceCollector` into `run_turn()` and `_execute_tool_calls()`.

- Wrap each `messages.create()` call in `trace.span("anthropic_api")`. After the call, add `response.usage.input_tokens`, `response.usage.output_tokens`, `response.model`, and `response.stop_reason` to the span metadata.
- Wrap the `asyncio.gather()` in `trace.span("tool_batch")`.
- Wrap each `_call_one()` in `trace.span("tool_call")` with tool name, node, and `command` (extracted from `block.input`). After the call returns, parse `cli_execution_ms` from the MCP JSON response and add `response_bytes`.

### 3. Instrument `mcp_pool.py`

Minimal change — `call_tool()` already returns the raw text. The caller (anthropic_client) will parse the JSON to extract `execution_time_ms` for the trace. No changes needed to mcp_pool.py itself.

### 4. Instrument `app.py`

In the `chat()` endpoint:
- Create a `TraceCollector` at request entry.
- Pass it through to `run_turn()`.
- After `run_turn()` returns, finalize the trace.
- Include `trace` in the JSON response alongside `response` and `session_id`.

### 5. Display in the UI

Extend `chat.html` to show a collapsible timing summary below each assistant message.

Render something like:

```
Total: 4,832ms | 2 rounds | 3 tool calls
├─ Round 1: API 2,312ms (in:1,204 out:389 tokens) → 3 tools (parallel, 1,102ms)
│  ├─ dns_node_a__statmon: querystore.top-clients duration=3600 — 1,102ms (CLI: 847ms, 2.3KB)
│  ├─ dns_node_b__statmon: querystore.top-clients duration=3600 — 934ms (CLI: 612ms, 1.8KB)
│  └─ dns_node_c__statmon: querystore.count duration=300 — 891ms (CLI: 503ms, 1.1KB)
└─ Round 2: API 1,418ms (in:2,847 out:512 tokens)
```

Implementation: check for `data.trace` in the fetch response, render a `<details>` element with `<summary>` showing the one-liner and the tree inside.

## Files changed

| File | Change |
|---|---|
| `statmon-chat/statmon_chat/trace.py` | **New** — TraceCollector, Span, RoundTrace, TurnTrace dataclasses |
| `statmon-chat/statmon_chat/anthropic_client.py` | Accept trace param, wrap API calls and tool calls in spans |
| `statmon-chat/statmon_chat/app.py` | Create TraceCollector, pass to run_turn, include trace in response |
| `statmon-chat/statmon_chat/templates/chat.html` | Render collapsible trace below assistant messages |
| `tests/test_trace.py` | Unit tests for TraceCollector |
| `tests/test_anthropic_client.py` | Update existing tests for new trace parameter |

## What this does NOT include

- **No external tracing libraries** (OpenTelemetry, Jaeger, etc.) — pure stdlib, no new dependencies
- **No MCP server changes** — cli_executor.py already returns `execution_time_ms`; we just surface it
- **No streaming** — the current architecture sends the full response at once; trace is included in that response
- **No persistent storage of traces** — traces are returned in the API response only, not logged to disk

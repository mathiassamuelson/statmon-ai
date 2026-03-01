# Implementation Plan

This plan translates `docs/design.md` into concrete implementation steps. It follows the four milestones from the design doc but breaks them into finer-grained tasks with technical decisions called out.

---

## Key Note: Statmon CLI Interface

The design doc and this plan both use the real Statmon `querystore.*` CLI interface documented in `docs/statmon-prompt.txt`. Key characteristics to keep in mind during implementation:

- **Invocation** is via `nom-tell`: `/usr/local/nom/sbin/nom-tell statmon querystore.<command> [args...]`
- **Commands** use `querystore.*` namespace (e.g., `querystore.top-clients`, `querystore.count`, `querystore.replay`)
- **Filters** use S-expression syntax: `"((and ((result-code (true (nxdomain))) (client-network (true ((netblock 10.0.0.0/24)))) )))"`
- **Time windows** use `duration <seconds>` or `interval ("YYYY-MM-DD:HH:MM:SS", "...")`
- **Result limits** use `max-results <n>` (default 20)
- **CLI executor** must use `shlex.split()` to preserve quoted S-expression filter strings when splitting arguments. The executor receives a command string like `querystore.top-clients duration 3600` and prepends the configured binary and subsystem, executing: `nom-tell statmon querystore.top-clients duration=3600`
- **No built-in security/threat commands** — threat detection (DDoS, DGA, tunneling) must be composed from filters on `querystore.group-count`, `querystore.top-clients`, `querystore.replay`, etc.

---

## Pre-Implementation Decisions

### 1. MCP SDK API surface

The `mcp` Python package has gone through several API revisions. Before writing code, we need to pin a version and verify:

- **Server side:** `from mcp.server import Server`, the `@app.tool()` decorator pattern, and `SseServerTransport` — confirm these exist in the installed version.
- **Client side:** `from mcp.client.sse import sse_client` as an async context manager, `ClientSession.initialize()`, `list_tools()`, `call_tool()` — confirm these exist.
- **Transport:** The design uses SSE (Server-Sent Events) over HTTP. Confirm whether the MCP SDK's SSE transport requires Starlette/ASGI integration or provides its own HTTP server.

**Action:** Install `mcp[server,client]`, inspect the package API, and adjust import paths if needed before writing any MCP code.

### 2. MCP client connection lifecycle

The design shows `async with sse_client(url)` as a context manager. For the chat app, we need *persistent* connections to MCP servers that survive across multiple user requests. Two approaches:

- **Option A: Keep context managers open** — Enter the `sse_client` context managers during FastAPI's `lifespan` startup and exit them on shutdown. This keeps WebSocket/SSE connections alive.
- **Option B: Connect-per-request** — Open a fresh MCP client connection for each tool call. Simpler but adds latency.

**Recommendation:** Option A. Use FastAPI's `lifespan` to manage MCP connections. If a connection drops, implement reconnection logic.

### 3. Session / conversation state

For the prototype, each browser session gets an in-memory conversation history list. Options:

- **Server-side sessions:** FastAPI stores conversation history in a dict keyed by session ID (cookie-based). Simple, no persistence across restarts.
- **Client-side state:** The browser sends the full conversation history with each request. Avoids server-side state but means large payloads.

**Recommendation:** Server-side sessions with a simple in-memory dict. Generate a session ID cookie on first request. Conversation history lives in memory — lost on restart, which is acceptable for a prototype.

### 4. Web UI approach

The design suggests HTMX or React. For a prototype:

**Recommendation:** Plain HTML + vanilla JavaScript with `fetch()`. No build tools, no framework. A single `chat.html` template served by FastAPI with Jinja2. The JS sends messages to `/api/chat`, receives the response, and appends it to the conversation div. This is the fastest path to a working UI.

### 5. setup.sh cleanup

The current `setup.sh` installs PyTorch with CUDA — this is left over from the workspace template and irrelevant to this project. It should be rewritten to just create the venv and install requirements. Also, this project targets Ubuntu (Linode) but should work on macOS for local dev, so remove `apt-get` and just do Python setup.

---

## Phase 1: Project Scaffolding & Mock CLIs (Milestone 1, part 1)

### 1.1 Create directory structure

```
statmon-mcp/
  statmon_mcp/
    __init__.py
statmon-chat/
  statmon_chat/
    __init__.py
    templates/
  static/
mock-cli/
dev/
configs/
```

### 1.2 Create per-component `pyproject.toml`

**`statmon-mcp/pyproject.toml`:**
```toml
[project]
name = "statmon-mcp"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "mcp[server]",
    "pyyaml",
    "starlette",
    "uvicorn",
]
```

**`statmon-chat/pyproject.toml`:**
```toml
[project]
name = "statmon-chat"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "anthropic",
    "mcp[client]",
    "fastapi",
    "uvicorn",
    "pyyaml",
    "jinja2",
]
```

Keep the root `requirements.txt` for dev tools only (pytest, black, flake8) plus editable installs of both components.

### 1.3 Write mock CLI scripts

One executable Python script in `mock-cli/` (CacheServe is deferred — see Risks and Open Questions #8):

**`mock-cli/statmon`** — Must implement the real `querystore.*` commands from `docs/statmon-prompt.txt`:

Activity commands:
- `querystore.top-clients` / `querystore.bottom-clients` — Top/bottom clients by query count
- `querystore.top-clients-by-request-size` / `querystore.top-clients-by-response-size` — By bandwidth
- `querystore.top-domains` / `querystore.bottom-domains` — Top/bottom queried domains
- `querystore.top-domains-by-request-size` / `querystore.top-domains-by-response-size` — By bandwidth
- `querystore.top-views` / `querystore.bottom-views` — By view

Aggregate commands:
- `querystore.count` — Query count
- `querystore.qps` — Queries per second
- `querystore.request-bandwidth` / `querystore.response-bandwidth` — Bandwidth totals
- `querystore.request-bandwidth-per-second` / `querystore.response-bandwidth-per-second` — Bandwidth rates

Analysis commands:
- `querystore.group-count` — Group by attribute (e.g., `group-by 'result-code'`)
- `querystore.replay` — Replay query stream (forensic analysis)
- `querystore.status` — Querystore system status

The mock must handle:
- Common arguments: `duration`, `max-results`, `filter`, `interval`, `source`
- S-expression filter strings: The mock doesn't need to fully parse these, but should accept them without error. Basic recognition (extracting domain or client-address from the filter for varying mock output) is a nice-to-have.
- Return JSON output that looks like realistic DNS query data (client IPs, domain names, query types, result codes, timestamps, byte counts).

The mock script should produce output with slightly randomized values (seeded by `NODE_NAME` environment variable) so results differ per node but are reproducible.

### 1.4 Write dev config files

Create `dev/config-node-a.yaml`, `dev/config-node-b.yaml`, and `dev/config-chat.yaml`. These follow the design doc section 7 structure:

```yaml
statmon:
  binary: "/usr/local/nom/sbin/nom-tell"
  subsystem: "statmon"
  timeout_seconds: 60
  rules:
    deny:
      - "querystore.reset"       # Destructive — clears all entries
    allow:
      - "querystore.*"           # All other querystore commands are read-only
```

The `binary` points to `nom-tell` and `subsystem` specifies the target (`statmon`). The CLI executor builds the full command as: `<binary> <subsystem> <command> [args...]`. For local dev with the mock CLI, set `binary` to the mock script path and `subsystem` to empty string (the mock accepts commands directly).

CacheServe is deferred — omit from dev configs for now, or include as a no-op stub.

### 1.5 Rewrite `setup.sh`

Remove PyTorch/CUDA steps. New flow:
1. Create venv at `~/statmon-ai`
2. `pip install -r requirements.txt`
3. `pip install -e ./statmon-mcp -e ./statmon-chat` (editable installs)
4. Verify imports work

---

## Phase 2: `statmon-mcp` Server (Milestone 1, part 2)

### 2.1 `statmon_mcp/filter.py` — Command allow/deny logic

- `glob_match(command: str, pattern: str) -> bool` — Case-insensitive glob matching using `fnmatch.fnmatch`. Handle the design's matching semantics: `*` matches a single segment, trailing ` *` matches any arguments.
- `check_command(command: str, rules: dict) -> tuple[bool, str]` — Deny-first, then allow, then default-deny.
- Unit tests in `tests/test_filter.py` covering:
  - Deny rules take precedence over allow rules
  - Allow rules match
  - Default deny when no rules match
  - Case insensitivity
  - Glob patterns: `*.statistics`, `querystore.*`, `dns.config show *`
  - Edge cases: empty command, command with extra whitespace

### 2.2 `statmon_mcp/cli_executor.py` — Subprocess execution

- `async def run_cli(binary: str, subsystem: str, command: str, timeout: int) -> dict` — Runs the CLI as a subprocess, captures stdout/stderr, measures execution time, handles timeouts.
- The executor builds the full command line as: `binary [subsystem] <command_args>`. In production this becomes `/usr/local/nom/sbin/nom-tell statmon querystore.top-clients duration=3600`. If `subsystem` is empty (e.g., mock CLI), it is omitted.
- Parse stdout as JSON if possible, fall back to raw string.
- Return the structured envelope from the design doc (status, exit_code, execution_time_ms, result/error).
- **Important: Argument handling with S-expression filters.** The real Statmon CLI uses commands like:
  ```
  nom-tell statmon querystore.top-clients duration=3600 filter="((query-type (true (A AAAA))))"
  ```
  The quoted S-expression filter contains spaces and parentheses. Naive `command.split()` would break this. Use `shlex.split()` which handles quoted substrings correctly. Verify this works with the S-expression examples from `docs/statmon-prompt.txt`.

### 2.3 `statmon_mcp/server.py` — MCP server with tool handlers

- Load config from `/etc/statmon-mcp/config.yaml` (overridable via `STATMON_MCP_CONFIG` env var for flexibility).
- Create the `statmon` MCP tool following the design's tool definition. CacheServe is deferred; the server should only expose `statmon` for now.
- Each tool handler: check command filter → run CLI → wrap in response envelope with node name.
- Wire up the MCP `Server` with SSE transport via Starlette/ASGI.
- The server itself is a Starlette ASGI app so that `uvicorn` can run it directly.

**Key detail:** The MCP Python SDK's `SseServerTransport` needs to be mounted into a Starlette app. The pattern is roughly:
```python
from starlette.applications import Starlette
from starlette.routing import Route
transport = SseServerTransport("/messages")
# Mount SSE endpoint and message endpoint as routes
```
We need to verify the exact API during implementation.

### 2.4 `statmon-mcp/Dockerfile`

As specified in the design doc. Python 3.12-slim base, install from pyproject.toml, expose port 8100, run with uvicorn.

### 2.5 Tests

- `tests/test_filter.py` — Unit tests for command filtering (see 2.1)
- `tests/test_cli_executor.py` — Test subprocess execution against the mock CLIs
- `tests/test_mcp_server.py` — Integration test: start the MCP server, connect an MCP client, call tools, verify responses

---

## Phase 3: `statmon-chat` Application (Milestone 2)

### 3.1 `statmon_chat/mcp_pool.py` — MCP client pool

- `MCPPool` class that manages connections to all configured MCP nodes.
- `async def connect_all(config)` — Connect to each node's MCP SSE endpoint, run `session.initialize()`, discover tools.
- `build_tool_registry()` — Create the prefixed tool name → node mapping. Store: `{prefixed_name: (node_connection, original_tool_name)}`.
- `build_anthropic_tools() -> list[dict]` — Convert the MCP tool definitions to Anthropic API format with node-prefixed names and descriptions.
- `async def call_tool(prefixed_name: str, arguments: dict) -> str` — Route to the correct MCP server, call the original tool name, return result.
- `async def disconnect_all()` — Clean shutdown.
- Handle connection failures gracefully: if a node is unreachable, log a warning and exclude it from the registry rather than crashing.

**Lifecycle concern:** The `sse_client()` context manager from the MCP SDK yields streams that must stay open. We need to enter these context managers and hold references to them. Use `contextlib.AsyncExitStack` to manage multiple context managers within the FastAPI lifespan.

### 3.2 `statmon_chat/anthropic_client.py` — Conversation loop

- `AnthropicChat` class wrapping the Anthropic client.
- `async def run_turn(conversation: list[dict], tools: list[dict], mcp_pool: MCPPool) -> str` — Implements the conversation loop from the design:
  1. Call Anthropic API with conversation + tools
  2. If response has `tool_use` blocks, execute each via `mcp_pool.call_tool()`
  3. Append tool results and call API again
  4. Loop until no more tool_use blocks
  5. Return final text
- Use the async Anthropic client (`anthropic.AsyncAnthropic`) since the rest of the app is async.
- **Guard against infinite loops:** Set a max iteration count (e.g., 10 tool-call rounds per turn). If exceeded, return an error message.
- Error handling: If a tool call fails (MCP connection error), return a tool_result with `is_error=True` so Claude can gracefully handle it.
- **Response truncation:** Before sending tool results to the Anthropic API, truncate any result exceeding 15KB. Append a note like `"\n\n[truncated — original size: {n}KB]"` so the LLM knows the output was clipped.

### 3.3 `statmon_chat/system_prompt.py` — Prompt builder

- `build_system_prompt(nodes: list[NodeInfo]) -> str` — Constructs the full system prompt.
- The static part covers:
  - **Statmon Querystore CLI reference** — Use `docs/statmon-prompt.txt` as the basis. This is the real CLI documentation and should be included nearly verbatim in the system prompt.
  - **CacheServe** — Deferred. Include a placeholder note in the system prompt indicating CacheServe support is not yet available.
- The dynamic part (available nodes list) is injected based on the currently connected nodes from the MCP pool.
- **Investigation patterns** — The design doc already has these written with real `querystore.*` commands. Use them as the basis for the system prompt's investigation guidance.
- Keep the prompt in a separate file (`statmon_chat/prompt.txt` or similar) for easy iteration without code changes.

### 3.4 `statmon_chat/app.py` — FastAPI web app

Routes:
- `GET /` — Serve `chat.html` template via Jinja2
- `POST /api/chat` — Accept `{message: str, session_id: str?}`, run conversation turn, return `{response: str, session_id: str}`
- `GET /api/nodes` — Return list of connected nodes and their status
- `GET /api/health` — Health check

FastAPI lifespan:
- On startup: Load config, create MCPPool, connect to all nodes, build tool registry, create AnthropicChat instance.
- On shutdown: Disconnect MCPPool.

Session management:
- In-memory dict: `sessions: dict[str, list[dict]]` mapping session_id → conversation history.
- Generate UUID session_id on first request if none provided.
- Consider a simple TTL eviction (e.g., drop sessions older than 1 hour) to prevent memory leaks. This can be a simple check on each request — no background task needed for the prototype.

### 3.5 `statmon-chat/Dockerfile`

As specified in the design. Python 3.12-slim, install from pyproject.toml, copy templates and static files, expose port 8443, run with uvicorn.

### 3.6 Tests

- `tests/test_anthropic_client.py` — Test the conversation loop with a mocked Anthropic API (mock the `messages.create` method to return scripted responses with and without tool calls).
- `tests/test_mcp_pool.py` — Test tool registry building, tool routing, connection error handling.
- `tests/test_app.py` — FastAPI test client integration tests for the API routes.

---

## Phase 4: Docker Compose & Integration (Milestone 2, continued)

### 4.1 `docker-compose.yaml`

As specified in the design doc. Three services: `mcp-node-a`, `mcp-node-b`, `chat`. Use the `statmon-net` bridge network so containers can reference each other by hostname.

### 4.2 `.env.example`

Template showing required environment variables:
```
ANTHROPIC_API_KEY=sk-ant-...
```

### 4.3 Integration testing

Manual verification workflow:
1. `docker compose up --build`
2. Hit `http://localhost:8443/api/health` — verify chat app is up
3. Hit `http://localhost:8443/api/nodes` — verify both MCP nodes are connected
4. Open `http://localhost:8443/` — send a test message like "What's the cache hit ratio on all nodes?"
5. Verify Claude calls tools on both nodes and synthesizes a response

### 4.4 Config templates in `configs/`

Production-oriented config templates:
- `configs/mcp-server.example.yaml` — Points to real binary paths
- `configs/chat-app.example.yaml` — Points to real MCP node IPs

---

## Phase 5: Web UI (Milestone 4, part 1)

### 5.1 `statmon_chat/templates/chat.html`

Minimal chat interface:
- Header bar showing app name and connected node count
- Scrollable message area with user/assistant message bubbles
- Input box + send button at the bottom
- Loading indicator while waiting for response
- JavaScript: `fetch('/api/chat', ...)` on send, append response to chat area
- Store session_id in a JS variable (received from first API response)

### 5.2 `statmon-chat/static/style.css`

Minimal styling. Dark theme (carrier NOC aesthetic). Monospace font for tool output. Responsive layout.

### 5.3 Rendering tool call details (optional enhancement)

When the assistant response includes information about which tools were called, render a collapsible "Tool Calls" section showing which nodes were queried. This is a nice-to-have for the prototype — engineers like seeing what's happening under the hood.

To support this, the `/api/chat` response could include structured metadata about tool calls made during the turn, not just the final text.

---

## Implementation Order

The recommended implementation order, factoring in dependencies:

```
1. Project scaffolding (Phase 1.1, 1.2, 1.4, 1.5)
2. Mock CLIs (Phase 1.3)
3. Command filter (Phase 2.1) + tests
4. CLI executor (Phase 2.2) + tests
5. MCP server (Phase 2.3, 2.4) + tests
6. ── Verify: docker run the MCP server, connect with MCP inspector ──
7. MCP client pool (Phase 3.1)
8. System prompt (Phase 3.3)
9. Anthropic conversation loop (Phase 3.2) + tests
10. FastAPI app (Phase 3.4, 3.5) + tests
11. Docker Compose (Phase 4.1, 4.2)
12. ── Verify: full stack via docker compose, test via API ──
13. Web UI (Phase 5.1, 5.2)
14. ── Verify: end-to-end from browser ──
```

Steps 1-6 can be built and verified without an Anthropic API key. Steps 7-14 require a key (or mocking the API for tests).

---

## Risks and Open Questions

1. **MCP SDK stability:** The `mcp` Python package is relatively new. The API shown in the design doc may not match the installed version exactly. We should install it early and verify the actual API surface before writing code that depends on it.

2. **SSE transport details:** The exact wiring of `SseServerTransport` into a Starlette/ASGI app isn't fully specified in the design. The MCP SDK examples should clarify this, but it may require some experimentation.

3. **Persistent MCP client connections:** Holding SSE connections open across the lifetime of the FastAPI app requires careful resource management. If a connection drops (MCP server restarts), we need reconnection logic. For the prototype, "reconnect on next request failure" is acceptable.

4. **Tool name validation:** The Anthropic API has rules about tool name format (e.g., `^[a-zA-Z0-9_-]{1,64}$`). Verify that `dns_node_a__cacheserve` is accepted. The double underscore should be fine.

5. **Response size:** If a Statmon query returns a very large result (thousands of query log entries), the tool result content sent to the Anthropic API could be large. **Decision:** Truncate tool results to 15KB with a note appended indicating the output was truncated and the original size.

6. **Concurrent tool calls:** The Anthropic API can return multiple `tool_use` blocks in a single response (e.g., querying both nodes simultaneously). The chat app should execute these in parallel (`asyncio.gather`) rather than sequentially for better latency.

7. **S-expression filter complexity:** The Statmon filter syntax (`"(and ( (result-code (true (nxdomain))) ... ))"`) is non-trivial. Two implications:
   - The LLM must learn to construct these filters correctly. The system prompt must include enough examples (the 5 examples in `statmon-prompt.txt` are a good start). We may need to iterate on the prompt to get reliable filter generation.
   - The CLI executor must preserve quoted filter strings intact when splitting arguments. `shlex.split()` handles this, but we need tests confirming it works with the real S-expression examples.

8. **CacheServe CLI deferred:** **Decision:** Only the Statmon (`querystore.*`) tool is implemented for now. CacheServe is deferred until the real CLI reference documentation is obtained. The MCP server exposes only the `statmon` tool, the mock CLI only covers `statmon`, and the system prompt marks CacheServe as not yet available. When CacheServe is added later, it will need: a real CLI reference doc, mock CLI script, allow/deny config rules, MCP tool handler, and system prompt section.

# Statmon Aggregator — Design Specification v1

## 1. Overview

The Statmon Aggregator is a natural-language chatbot that enables carrier engineers to query across multiple CacheServe DNS servers and their co-located Statmon log collectors from a single interface. It replaces the current workflow of SSH-ing into individual nodes to run CLI queries.

### Architecture Summary

```
┌──────────────┐       HTTPS        ┌────────────────────────────────────┐
│   Browser    │◄──────────────────►│   Chat App (statmon-chat)          │
│  (Laptop)    │                    │                                    │
└──────────────┘                    │  ┌──────────────────────────────┐  │
                                    │  │  Anthropic API Client        │  │
                                    │  │  - Rich system prompt        │  │
                                    │  │  - Tool definitions from MCP │  │
                                    │  │  - Conversation state        │  │
                                    │  └──────────┬───────────────────┘  │
                                    │             │                      │
                                    │  ┌──────────▼───────────────────┐  │
                                    │  │  MCP Client Pool             │  │
                                    │  │  - Connects to all nodes     │  │
                                    │  │  - Routes tool calls         │  │
                                    │  │  - Prefixes tools with node  │  │
                                    │  └──┬───────────────────────┬───┘  │
                                    └─────┼───────────────────────┼──────┘
                                          │                       │
                                      SSE/HTTP                SSE/HTTP
                                          │                       │
                                 ┌────────▼─────────┐   ┌─────────▼────────┐
                                 │  Node A          │   │  Node B          │
                                 │  (statmon-mcp)   │   │  (statmon-mcp)   │
                                 │  ┌────────────┐  │   │  ┌────────────┐  │
                                 │  │ cacheserve │  │   │  │ cacheserve │  │
                                 │  │ statmon    │  │   │  │ statmon    │  │
                                 │  └────────────┘  │   │  └────────────┘  │
                                 │                  │   │                  │
                                 │  CacheServe DNS  │   │  CacheServe DNS  │
                                 │  Statmon Logs    │   │  Statmon Logs    │
                                 └──────────────────┘   └──────────────────┘
```

### Deployment Environment

- All components run inside a Linode VPC (private network)
- MCP servers listen on private IPs only
- Chat app is the only component reachable from outside (whitelisted IP)
- Network-level isolation is the initial security model

### Components

| Component | Runs On | Count | Purpose |
|-----------|---------|-------|---------|
| `statmon-mcp` | Each DNS node | N (2 for prototype) | MCP server exposing CLI tools |
| `statmon-chat` | Dedicated VM in VPC | 1 | Web UI + Anthropic API + MCP client |

---

## 2. Component: `statmon-mcp` (MCP Server)

### Purpose

Runs on each CacheServe/Statmon node. Exposes MCP tools that execute CLI commands locally, subject to a configurable allow/deny filter.

### Configuration

The MCP server is configured via YAML with three sections:

- **server** — bind address, port, and a unique node name identifier
- **cacheserve** — path to the CacheServe CLI binary, timeout, and allow/deny rules
- **statmon** — path to the Statmon CLI binary (`nom-tell`), subsystem name, timeout, and allow/deny rules

Config is loaded from (in order): `STATMON_MCP_CONFIG` env var, `~/.config/statmon-mcp/config.yaml`, `/etc/statmon-mcp/config.yaml`. See `configs/mcp-server.example.yaml` for a template.

### Command Filter Logic

```
function is_allowed(command, rules):
    # Step 1: Check deny list first (deny takes precedence)
    for pattern in rules.deny:
        if glob_match(command, pattern):
            return DENIED, "Command matches deny rule: {pattern}"

    # Step 2: Must match at least one allow pattern
    for pattern in rules.allow:
        if glob_match(command, pattern):
            return ALLOWED

    # Step 3: Default deny (whitelist approach)
    return DENIED, "Command does not match any allow rule"
```

Glob matching rules:
- `*` matches any sequence of characters (e.g., `*.statistics` matches `cache.statistics`)
- Matching is case-insensitive
- Deny rules prevent destructive operations (flush, clear, reset, shutdown, etc.)
- Allow rules whitelist read-only operations (statistics, status, queries, etc.)

### MCP Tool Definitions

Each tool takes a single `command` string parameter and returns a JSON envelope containing:
- `node` — which node executed the command
- `tool` — which tool was called
- `command` — the command that was executed
- `status` — `success`, `denied`, or `error`
- `exit_code` and `execution_time_ms` — for successful executions
- `result` — the CLI's JSON output (on success)
- `error` — error message (on failure or denial)

### CLI Execution

Commands are executed via `asyncio.create_subprocess_exec` with configurable timeouts. The executor uses `shlex.split()` to correctly handle complex argument strings (including S-expression filter syntax with spaces and parentheses in quoted arguments).

---

## 3. Component: `statmon-chat` (Chat Application)

### Purpose

Web application that provides the chat interface, manages MCP client connections to all nodes, and mediates between the user and the Anthropic API.

### Configuration

Configured via YAML with sections for:

- **server** — bind address and port
- **anthropic** — model name and max tokens
- **nodes** — list of MCP node names and URLs

Config is loaded from (in order): `STATMON_CHAT_CONFIG` env var, `~/.config/statmon-chat/config.yaml`, `/etc/statmon-chat/config.yaml`. See `configs/chat-app.example.yaml` for a template.

### MCP Client — Tool Discovery and Routing

On startup, the chat app connects to each MCP server, discovers its tools, and builds a combined tool registry with node-prefixed names:

```
MCP Server dns-node-a exposes: cacheserve, statmon
MCP Server dns-node-b exposes: cacheserve, statmon

Combined tool registry for Anthropic API:
  dns_node_a__cacheserve  → routes to dns-node-a MCP server
  dns_node_a__statmon     → routes to dns-node-a MCP server
  dns_node_b__cacheserve  → routes to dns-node-b MCP server
  dns_node_b__statmon     → routes to dns-node-b MCP server
```

Tool descriptions sent to the Anthropic API include the node name, so the LLM knows which node each tool targets.

### Conversation Loop

```
User sends message
        │
        ▼
Chat app builds Anthropic API request:
  - system prompt (command documentation)
  - conversation history
  - tool definitions (from MCP discovery)
        │
        ▼
Call Anthropic API
        │
        ▼
Response contains tool_use blocks? ──── No ──► Display text to user
        │
       Yes
        │
        ▼
For each tool_use block (in parallel):
  1. Parse node name from tool name prefix
  2. Route to correct MCP server
  3. Collect MCP tool result
        │
        ▼
Build tool_result messages
        │
        ▼
Call Anthropic API again (with tool results)
        │
        ▼
Loop until response has no more tool_use blocks
(max 10 rounds as a safety guard)
        │
        ▼
Display final text to user
```

### Request Tracing

Each chat request is instrumented with timing spans:
- **API call spans** — duration, token counts (input/output), stop reason
- **Tool batch spans** — duration of parallel tool execution
- **Tool call spans** — per-tool duration, CLI execution time, response size, node name

The trace is returned with the chat response and rendered in the UI as a collapsible tree showing the full breakdown of where time was spent.

### Web Interface

Minimal chat UI using FastAPI + Jinja2 templates with markdown rendering (marked.js). Routes:

```
GET  /                  → Chat UI
POST /api/chat          → Send message, get response (with trace)
GET  /api/nodes         → List connected nodes and their status
GET  /api/health        → Health check
```

---

## 4. System Prompt

The system prompt is the core of the LLM's understanding. It contains man-page-style documentation for all available commands, query patterns, and operational guidance.

The system prompt template lives in `statmon-chat/statmon_chat/prompt.txt` and includes:

- **Available Nodes** — dynamically injected at startup based on connected MCP servers
- **Tool Usage Guidelines** — parallel querying, duration advice, domain vs name guidance
- **CLI Reference Documentation** — command syntax, arguments, filter syntax (proprietary; not included in the public repository)
- **Investigation Patterns** — health check, SERVFAIL drill-down, DDoS/PRSD detection, amplification attacks, performance, malware/C2, forensic replay

The chat app injects the current node list into the `{nodes_section}` placeholder at conversation start.

See `README.md` for instructions on providing your own CLI reference documentation.

---

## 5. Project Structure

```
statmon-ai/
├── README.md
├── CLAUDE.md
├── docker-compose.yaml            # Full stack: chat + 2 MCP nodes
├── setup.sh                       # Environment setup script
├── statmon-mcp/                   # MCP server (runs on each node)
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── statmon_mcp/
│       ├── __init__.py
│       ├── server.py              # MCP server + tool handlers
│       ├── filter.py              # Command allow/deny logic
│       └── cli_executor.py        # Subprocess execution (shlex-based)
├── statmon-chat/                  # Chat application
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── statmon_chat/
│   │   ├── __init__.py
│   │   ├── app.py                 # FastAPI web app
│   │   ├── mcp_pool.py            # MCP client pool + tool registry
│   │   ├── anthropic_client.py    # API client + conversation loop
│   │   ├── trace.py               # Request-level timing instrumentation
│   │   ├── system_prompt.py       # Prompt builder
│   │   ├── prompt.txt             # System prompt template
│   │   └── templates/
│   │       └── chat.html          # Web UI (markdown rendering via marked.js)
│   └── static/
│       └── style.css
├── configs/                       # Configuration file templates
│   ├── mcp-server.example.yaml
│   └── chat-app.example.yaml
├── tests/                         # Test suite
│   ├── test_cli_executor.py
│   ├── test_filter.py
│   ├── test_app.py
│   ├── test_anthropic_client.py
│   └── test_mcp_pool.py
└── docs/
    └── design.md                  # This document
```

---

## 6. Docker Deployment

Both components deploy as Docker containers. The MCP server container runs on each DNS node alongside CacheServe/Statmon, and the chat app runs on a dedicated VM (or locally on macOS).

### Production Deployment (Linode VPC)

**On each DNS node:**
```bash
docker run -d \
  --name statmon-mcp \
  --restart unless-stopped \
  -v /etc/statmon-mcp/config.yaml:/etc/statmon-mcp/config.yaml:ro \
  -p 8100:8100 \
  statmon-mcp:latest
```

The production config points `binary` at the real CLI paths, which are bind-mounted into the container or available via `--network host`.

**On the chat VM:**
```bash
docker run -d \
  --name statmon-chat \
  --restart unless-stopped \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -v /etc/statmon-chat/config.yaml:/etc/statmon-chat/config.yaml:ro \
  -p 8443:8443 \
  statmon-chat:latest
```

### docker-compose (Development)

`docker-compose up` brings up the full stack: two MCP nodes and the chat app on a shared Docker network. Config files are mounted from the host. See `docker-compose.yaml` and `configs/` for details.

---

## 7. Key Design Decisions

- **MCP tool names are prefixed with node name** (e.g., `dns_node_a__statmon`) so the LLM can explicitly target specific nodes and issue parallel cross-node queries
- **Command filtering uses deny-first, then allow-list, then default-deny** — safe by default, with deny rules taking precedence over allow rules
- **The Anthropic API is used with a rich system prompt** containing full CLI documentation, rather than a fine-tuned model — this allows rapid iteration on the prompt without retraining
- **Claude acts as the orchestrator** — it handles cross-node correlation, aggregation, and investigation logic; no custom aggregation code is needed in the chat app
- **Tool results are truncated at 15KB** to prevent context window bloat from large query results
- **Parallel tool execution** via `asyncio.gather` minimizes latency when querying multiple nodes

---

## 8. Prototype Milestones

### Milestone 1: MCP Server with Command Filtering ✓
- `statmon-mcp` with deny/allow command filtering
- CLI execution via `nom-tell` with subsystem and key=value syntax
- Dockerfile and container verification

### Milestone 2: Chat App with Tool Routing ✓
- `statmon-chat` with MCP client pool
- Dockerfile for the chat app
- Tool discovery and prefixed naming across nodes
- Anthropic API conversation loop with tool calls

### Milestone 3: System Prompt + Investigation Flows ✓
- Full command documentation in the system prompt
- Investigation patterns (health check, SERVFAIL, DDoS/PRSD, amplification, performance, malware/C2)
- Markdown rendering in chat UI

### Milestone 4: Production Deployment ✓
- MCP servers running on production DNS nodes via `nom-tell`
- Chat app running locally on macOS connecting to remote MCP servers
- Config lookup: env var → `~/.config/` → `/etc/`

---

## 9. Future Enhancements

- **Domain investigation tools:** WHOIS, DNS resolution, IP geolocation, and web search directly in the chat app (see `docs/domain-investigation-tools-requirements.md`)
- **Threat intelligence:** VirusTotal, AbuseIPDB, and other API integrations
- **Authentication:** OIDC/OAuth2 on the MCP servers
- **Dynamic node discovery:** Service registry or DNS-based discovery
- **Local LLM:** Self-hosted option using vLLM
- **Fine-tuned model:** Trained on actual CacheServe/Statmon interactions
- **Audit logging:** Record all commands executed through the aggregator
- **Rate limiting:** Prevent the LLM from overwhelming nodes with queries
- **Streaming responses:** SSE from chat app for real-time token display
- **Multi-site support:** Extend to query across multiple carrier sites
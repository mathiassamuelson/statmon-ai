# DNS Operator Copilot — Design

A chat application that helps DNS operators investigate query traffic, performance issues, and security incidents on production DNS infrastructure. The operator asks questions in natural language; the application composes answers by calling tools across multiple sources.

## 1. The shape of the system

DNS Operator Copilot is a single FastAPI-based chat application that mediates between an operator and the Anthropic API. The application's distinguishing feature is how it sources its tool capabilities — three layers, all of which the language model treats uniformly:

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│                   DNS Operator Copilot (chat app)               │
│                                                                 │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────┐  │
│  │  Local tools    │  │  MCP-connected   │  │  Anthropic     │  │
│  │  (in-process)   │  │  tools (remote)  │  │  native tools  │  │
│  │                 │  │                  │  │                │  │
│  │  whois_lookup   │  │  statmon         │  │  web_search    │  │
│  │  dns_resolve    │  │  cacheserve      │  │                │  │
│  │  ip_geolocation │  │  ps, grep, …     │  │                │  │
│  │  reverse_dns    │  │  (catalog-       │  │                │  │
│  │                 │  │   driven)        │  │                │  │
│  └─────────────────┘  └──────────────────┘  └────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
        │                       │                       │
        ▼                       ▼                       ▼
   in-process            MCP servers on             Anthropic
   network calls         each DNS node              API backend
   (WHOIS, DNS,          (cli-mcp-server)
   IP geolocation)
```

Three capability sources, one consistent interface:

| Source | What it is | Where it runs |
|---|---|---|
| **Local tools** | Python functions registered with the conversation loop. WHOIS, DNS, IP geolocation, reverse DNS. | In the chat app itself, as direct network calls. |
| **MCP-connected tools** | Tools exposed by an MCP server reachable over the network. The canonical case is `cli-mcp-server` running on a DNS node, exposing whatever CLIs (Statmon, CacheServe, Linux diagnostics) the operator has wrapped. | On the remote host. The chat app speaks MCP-over-SSE to one or more servers. |
| **Anthropic native tools** | Tools the Anthropic API itself implements — currently web search. | Inside the Anthropic backend. |

The operator doesn't see these distinctions; the model does, via the tool list assembled at startup. Each layer can grow independently — new local tool: drop a Python function and a description file; new MCP tool: edit a catalog YAML on the DNS node; new native tool: enable it in the Anthropic SDK call.

This design replaces an earlier "Statmon AI Aggregator" architecture that conflated *the chat app* with *the MCP server*. The MCP server moved to a separate generic project — [cli-mcp-server](https://github.com/mathiassamuelson/cli-mcp-server) — once it became clear that wrapping a CLI behind a safety filter is a problem with a life beyond DNS.

## 2. Chat application internals

### 2.1 Lifecycle

At startup, the application:

1. Loads its config (Anthropic model, MCP nodes to connect to, prompt path, local-tool config).
2. Connects to each configured MCP server, discovers the tools it offers, prefixes them with the node name (e.g., `dns_node_a__statmon`).
3. Registers local tools from `copilot/security_tools.py`, loading each tool's description from `copilot/descriptions/<name>.md`.
4. Builds the system prompt by injecting the connected-nodes list into a template.
5. Starts the FastAPI server.

On shutdown, it disconnects from each MCP server cleanly (suppressing the asyncio teardown noise the MCP SDK produces).

### 2.2 Per-request flow

When an operator sends a message:

1. The chat app retrieves or creates a session, loads its conversation history.
2. It composes the Anthropic API request: system prompt, conversation history, full tool list (local + MCP-prefixed + native).
3. The API responds with either a final answer (passed to the UI as Markdown) or one-or-more `tool_use` blocks.
4. For each `tool_use`: dispatch to the correct handler (local function, MCP `call_tool`, or — for native tools — let the API handle it server-side).
5. Tool results are collected, appended to the conversation, and the request is re-sent.
6. Loop until the API stops requesting tools.

A request-level trace collector records every API round, every tool call, and timing — surfaced in the UI for inspection.

### 2.3 Modules

```
copilot/copilot/
├── app.py                # FastAPI lifespan, routes
├── anthropic_client.py   # Conversation loop, tool dispatch
├── mcp_pool.py           # Per-node MCP connections, reconnection logic
├── security_tools.py     # Local tool implementations + registration
├── system_prompt.py      # Prompt template loader, node-list injection
├── trace.py              # Per-request timing and tool-call instrumentation
├── log_filters.py        # Quiets MCP SDK teardown noise
├── cli.py                # Headless CLI for automated conversations
├── prompt.txt            # System prompt template
├── descriptions/         # One markdown file per local tool
│   ├── whois_lookup.md
│   ├── dns_resolve.md
│   ├── ip_geolocation.md
│   └── reverse_dns_lookup.md
└── templates/
    └── chat.html         # Web UI
```

### 2.4 Tool descriptions as documents

Each tool — local or MCP-side — has its description maintained as a standalone Markdown file rather than as a string literal in code. The agent reads these descriptions to decide *when* and *how* to use each tool.

This pattern lives on both sides:

- **Local tools:** `copilot/descriptions/<tool_name>.md` is loaded at module import time. A missing file is a hard import-time error — better than a silent fallback that ships a tool with no documentation.
- **MCP tools:** the catalog entry's `description_file:` field points at a Markdown file resolved relative to the catalog directory. See [cli-mcp-server's documentation](https://github.com/mathiassamuelson/cli-mcp-server) for the catalog format.

The system prompt's job, after this design, is *orchestration across tools* — not *teaching the model each tool's syntax*. That responsibility belongs to the per-tool descriptions.

## 3. Configuration

Loaded in priority order:

1. `COPILOT_CONFIG` environment variable (full path)
2. `~/.config/copilot/config.yaml`
3. `/etc/copilot/config.yaml`

See `configs/chat-app.example.yaml` for the full schema. The major sections:

- **`anthropic`** — model name, max tokens, API key (via `ANTHROPIC_API_KEY` env var).
- **`nodes`** — list of MCP servers to connect to. Each has a name (used as the tool prefix) and a URL.
- **`security_tools`** — per-tool config for local tools (DNS resolver choice, geolocation provider, etc.).
- **`prompt_path`** — override the default `copilot/prompt.txt`. Production deployments typically point this at a deployment-specific prompt with proprietary DNS-tool references.

## 4. The system prompt

The shipped `prompt.txt` covers orchestration concerns:

- Cross-node behavior — always query all nodes for site-wide questions, run in parallel.
- Investigation pacing — short durations for active problems, scope checks before deep queries.
- Reporting style — lead with the answer, surface disagreements between nodes explicitly.
- Cross-tool investigation patterns — Suspicious Domain, Suspicious Client, DGA/PRSD Analysis, C2/Botnet Infrastructure. These compose multiple tools and don't fit in any single tool's description.

Per-tool syntax (which is the bulk of what an operator-focused prompt needs) lives in the tool descriptions, not the prompt.

The placeholder `{nodes_section}` is replaced at startup with the connected-node list. The rest of the prompt is static.

## 5. Deployment

The chat app is intended to run wherever the operator works — typically a laptop or a single VM with network reachability to the MCP nodes. The MCP nodes themselves run on the DNS infrastructure they observe.

- **MCP nodes:** see [cli-mcp-server](https://github.com/mathiassamuelson/cli-mcp-server). One instance per DNS node, configured with a catalog that exposes whichever CLIs that node should make available.
- **Chat app:** `pip install -e ./copilot`, set `ANTHROPIC_API_KEY` and `COPILOT_CONFIG`, run `bin/chat-server.sh`.

There is no required container orchestration. The chat app is a single process; the MCP servers are independent. Docker is supported for the chat app but not required — most operators will run it natively.

## 6. Future direction

The project's longer arc is to move from the Anthropic API to a locally fine-tuned model, so that operator queries can be answered without an external API call (latency, cost, and data-residency reasons). The architecture above doesn't change: a fine-tuned model would replace the `AnthropicChat` client with a vLLM-served local model, and the tool-dispatch infrastructure stays as-is.

Training-data generation is already supported: `bin/chat-cli.sh` drives automated conversations against the running stack and captures full multi-turn JSONL with tool calls and results.

## 7. Project structure (top level)

```
dns-operator-copilot/
├── README.md
├── CLAUDE.md
├── setup.sh
├── pyproject.toml          # pytest config only
├── requirements.txt
├── bin/
│   ├── chat-server.sh      # Launch the FastAPI server
│   └── chat-cli.sh         # Headless automation entry
├── copilot/                # The chat application
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── copilot/            # Python package
├── configs/
│   └── chat-app.example.yaml
├── docs/
│   └── design.md           # This document
└── tests/                  # Pytest suite
```
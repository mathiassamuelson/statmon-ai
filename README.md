# StatMon AI Aggregator

**Goal:** Natural-language chatbot that enables carrier engineers to query across multiple CacheServe DNS servers and their co-located Statmon log collectors from a single interface.

**Platform:** macOS / Linux | Linode VPC for deployed MCP nodes

## Architecture

The chat application connects to one or more MCP servers (running on each DNS node) and mediates between the user and the Anthropic API. MCP servers expose CLI tools — `cacheserve` for DNS server management, `statmon` for query log analysis, and any other binary the operator wraps.

Tool names are prefixed with the node name (e.g., `dns_node_a__statmon`) so the LLM can target specific servers.

The MCP server lives in a separate repository: [cli-mcp-server](https://github.com/mathiassamuelson/cli-mcp-server). Deploy one instance per DNS node, configure the catalog, and point this chat app at the resulting URLs.

See [docs/design.md](docs/design.md) for the full design specification.

## Quick Start

```bash
# Clone repository
git clone https://github.com/mathiassamuelson/statmon-ai.git
cd statmon-ai

# Setup environment
./setup.sh

# Start the chat web server
export ANTHROPIC_API_KEY=sk-ant-...
bin/chat-server.sh
```

The helper scripts in `bin/` activate `.venv` and launch the chat app with sensible defaults:

- `bin/chat-server.sh` — starts `statmon-chat` on `127.0.0.1:8443` (override with `HOST`/`PORT`)
- `bin/chat-cli.sh` — drives automated conversations for LoRA training data generation

## Repository Structure

- **`statmon-chat/`** — Chat application (FastAPI + Anthropic API + MCP clients + agent-side investigation tools)
- **`docs/`** — Project documentation:
  - [`design.md`](docs/design.md) — Full design specification and architecture
  - `statmon-prompt.txt` — Statmon Querystore CLI reference (proprietary; not in public repo)
  - `StatmonExplainer.md` — Practical troubleshooting examples (proprietary; not in public repo)
- **`configs/`** — Configuration file templates

## Running the chat application

### Install dependencies

```bash
./setup.sh
source .venv/bin/activate
```

Or manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ./statmon-chat
```

### Run

Point the chat app at a config file via `STATMON_CHAT_CONFIG`, then launch:

```bash
export STATMON_CHAT_CONFIG=~/.config/statmon-chat/config.yaml
export ANTHROPIC_API_KEY=sk-ant-...
bin/chat-server.sh
```

`HOST` and `PORT` environment variables override the defaults (`127.0.0.1:8443`).

The config specifies the Anthropic model, the list of MCP nodes to connect to, the agent-side tool configuration, and the path to the system prompt. See `configs/chat-app.example.yaml` for a template.

## Setting up an MCP server

Deploy [cli-mcp-server](https://github.com/mathiassamuelson/cli-mcp-server) on each DNS node, configure its catalog with `cacheserve`, `statmon`, and any other tools you want to expose, then list the resulting URLs in the chat app's `nodes` config section.

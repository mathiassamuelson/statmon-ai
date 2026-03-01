# StatMon AI Aggregator

**Goal:** Natural-language chatbot that enables carrier engineers to query across multiple CacheServe DNS servers and their co-located Statmon log collectors from a single interface.

**Platform:** Ubuntu 24.04 | Linode VPC

## Architecture

Two components:

- **`statmon-mcp`** — MCP server running on each DNS node, exposing `cacheserve` and `statmon` CLI tools with configurable allow/deny filtering
- **`statmon-chat`** — Web app providing the chat UI, connecting to all MCP nodes, and mediating with the Anthropic API

The MCP server exposes two tools per node — `cacheserve` (DNS server management) and `statmon` (query log analysis via `querystore.*` commands with S-expression filter syntax). Tool names are prefixed with the node name (e.g., `dns_node_a__statmon`) so the LLM can target specific servers.

See [docs/design.md](docs/design.md) for the full design specification.

## Quick Start

```bash
# Clone repository
git clone https://github.com/mathiassamuelson/statmon-ai-aggregator.git
cd statmon-ai-aggregator

# Setup environment
./setup.sh
source ~/statmon-ai/bin/activate

# Run with Docker Compose (full stack with mock CLIs)
docker compose up
```

## Repository Structure

- **`statmon-mcp/`** — MCP server (runs on each DNS node)
- **`statmon-chat/`** — Chat application (FastAPI + Anthropic API + MCP clients)
- **`mock-cli/`** — Mock CacheServe/Statmon CLIs for local development
- **`docs/`** — Project documentation:
  - [`design.md`](docs/design.md) — Full design specification and architecture
  - [`plan.md`](docs/plan.md) — Implementation plan with phased milestones
  - [`statmon-prompt.txt`](docs/statmon-prompt.txt) — Statmon Querystore CLI reference (real command syntax)
- **`configs/`** — Configuration file templates

## Running without Docker

Both components can be run directly for development or on hosts where Docker isn't available.

### Install dependencies

```bash
./setup.sh
source ~/statmon-ai/bin/activate
```

Or manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ./statmon-mcp -e ./statmon-chat
```

### Using the mock CLI

To use the mock CLI instead of real Statmon binaries, make it executable and set `NODE_NAME`:

```bash
chmod +x mock-cli/statmon
export NODE_NAME=dns-node-a
```

Then ensure the MCP config points `binary` at `./mock-cli/statmon` (or the absolute path).

### MCP Server (`statmon-mcp`)

Point the server at a config file via `STATMON_MCP_CONFIG`:

```bash
export STATMON_MCP_CONFIG=./dev/config-node-a.yaml
uvicorn statmon_mcp.server:app --host 0.0.0.0 --port 8100
```

The config specifies the node name, statmon binary path, and allow/deny rules. See `dev/config-node-a.yaml` for an example. For production, copy `configs/mcp-server.example.yaml` to `/etc/statmon-mcp/config.yaml`.

Endpoints:
- `GET /mcp` — SSE endpoint for MCP client connections
- `POST /messages/` — MCP message handling
- `GET /health` — Health check

Verify:

```bash
curl http://localhost:8100/health
# {"status":"ok","node":"dns-node-a","tools":["statmon"]}
```

### Chat App (`statmon-chat`)

The chat app requires an Anthropic API key and at least one running MCP server to connect to.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export STATMON_CHAT_CONFIG=./dev/config-chat.yaml
uvicorn statmon_chat.app:app --host 0.0.0.0 --port 8443
```

The chat config specifies the Anthropic model, and the list of MCP node URLs to connect to. See `dev/config-chat.yaml` for an example. For production, copy `configs/chat-app.example.yaml` to `/etc/statmon-chat/config.yaml`.

Endpoints:
- `GET /` — Chat web UI
- `POST /api/chat` — Send a message, get a response
- `GET /api/nodes` — List connected MCP nodes
- `GET /api/health` — Health check

Verify:

```bash
curl http://localhost:8443/api/health
# {"status":"ok","nodes_connected":2,"tools_available":2}
```

### Running the full stack locally

Start two MCP servers (as separate terminals or background processes), then the chat app:

```bash
# Terminal 1: MCP node A
export STATMON_MCP_CONFIG=./dev/config-node-a.yaml
export NODE_NAME=dns-node-a
uvicorn statmon_mcp.server:app --host 0.0.0.0 --port 8101

# Terminal 2: MCP node B
export STATMON_MCP_CONFIG=./dev/config-node-b.yaml
export NODE_NAME=dns-node-b
uvicorn statmon_mcp.server:app --host 0.0.0.0 --port 8102

# Terminal 3: Chat app (update dev/config-chat.yaml node URLs to localhost:8101/8102 first)
export ANTHROPIC_API_KEY=sk-ant-...
export STATMON_CHAT_CONFIG=./dev/config-chat.yaml
uvicorn statmon_chat.app:app --host 0.0.0.0 --port 8443
```

Then open http://localhost:8443 in your browser.

## Development

```bash
pytest                        # Run tests
black .                       # Format code
flake8                        # Lint code
```

---

**Last Updated:** February 2026

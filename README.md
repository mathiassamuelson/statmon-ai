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
git clone https://github.com/mathiassamuelson/statmon-ai.git
cd statmon-ai

# Setup environment
./setup.sh

# Start the MCP server (on a DNS node)
bin/mcp-server.sh

# Start the chat web server (locally)
export ANTHROPIC_API_KEY=sk-ant-...
bin/chat-server.sh
```

The helper scripts in `bin/` activate `.venv` and launch each component with sensible defaults:

- `bin/mcp-server.sh` — starts `statmon-mcp` on `0.0.0.0:8100` (override with `HOST`/`PORT`)
- `bin/chat-server.sh` — starts `statmon-chat` on `127.0.0.1:8443` (override with `HOST`/`PORT`)
- `bin/chat-cli.sh` — drives automated conversations for LoRA training data generation

Or run the full stack with Docker Compose:

```bash
docker compose up
```

## Repository Structure

- **`statmon-mcp/`** — MCP server (runs on each DNS node)
- **`statmon-chat/`** — Chat application (FastAPI + Anthropic API + MCP clients)
- **`docs/`** — Project documentation:
  - [`design.md`](docs/design.md) — Full design specification and architecture
  - [`statmon-prompt.txt`](docs/statmon-prompt.txt) — Statmon Querystore CLI reference (real command syntax)
  - [`StatmonExplainer.md`](docs/StatmonExplainer.md) — Practical troubleshooting examples and patterns
- **`configs/`** — Configuration file templates

## Running without Docker

Both components can be run directly for development or on hosts where Docker isn't available.

### Install dependencies

```bash
./setup.sh
source .venv/bin/activate
```

Or manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ./statmon-mcp -e ./statmon-chat
```

### MCP Server (`statmon-mcp`)

Point the server at a config file via `STATMON_MCP_CONFIG`, then launch via the helper script:

```bash
export STATMON_MCP_CONFIG=/etc/statmon-mcp/config.yaml
bin/mcp-server.sh
```

`HOST` and `PORT` environment variables override the defaults (`0.0.0.0:8100`).

The config specifies the node name, binary path, and allow/deny rules. See `configs/mcp-server.example.yaml` for a template. Config is loaded from (in order): `STATMON_MCP_CONFIG` env var, `~/.config/statmon-mcp/config.yaml`, `/etc/statmon-mcp/config.yaml`.

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
export STATMON_CHAT_CONFIG=/etc/statmon-chat/config.yaml
bin/chat-server.sh
```

`HOST` and `PORT` environment variables override the defaults (`127.0.0.1:8443`).

#### Configuration

The chat config specifies the Anthropic model, the list of MCP node URLs to connect to, and optionally a custom system prompt file. See `configs/chat-app.example.yaml` for a full template. Config is loaded from (in order): `STATMON_CHAT_CONFIG` env var, `~/.config/statmon-chat/config.yaml`, `/etc/statmon-chat/config.yaml`.

Minimal example:

```yaml
server:
  host: "0.0.0.0"
  port: 8443

anthropic:
  model: "claude-sonnet-4-20250514"
  max_tokens: 4096

# Optional: absolute path to a custom system prompt template.
# If omitted, the bundled statmon-chat/statmon_chat/prompt.txt is used.
# The template may include a {nodes_section} placeholder, which is
# substituted at startup with the configured MCP node list.
prompt_path: "/etc/statmon-chat/prompt.txt"

nodes:
  - name: "dns-node-a"
    mcp_url: "http://10.0.1.10:8100/mcp"
  - name: "dns-node-b"
    mcp_url: "http://10.0.1.11:8100/mcp"
```

**System prompt.** The system prompt is the authoritative CLI reference and investigation-pattern guide shipped to Claude on every request. By default the app loads `statmon-chat/statmon_chat/prompt.txt` from the installed package. To override it — for example, to ship a proprietary prompt kept outside this repository — set `prompt_path` to an absolute path. The file is read once at startup, so restart the chat server after editing it.

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

## Development

```bash
pytest                        # Run tests
black .                       # Format code
flake8                        # Lint code
```

---

**Last Updated:** March 2026

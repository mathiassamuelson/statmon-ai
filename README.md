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

## Running the MCP Server (without Docker)

You can run the MCP server directly for development or on a host where Docker isn't available.

### 1. Install dependencies

```bash
./setup.sh
source ~/statmon-ai/bin/activate
```

Or manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ./statmon-mcp
```

### 2. Configure

Point the server at a config file via the `STATMON_MCP_CONFIG` environment variable:

```bash
export STATMON_MCP_CONFIG=./dev/config-node-a.yaml
```

The config specifies the node name, statmon binary path, and allow/deny rules. See `dev/config-node-a.yaml` for an example. For production, copy `configs/mcp-server.example.yaml` to `/etc/statmon-mcp/config.yaml` and update the binary path and node name.

### 3. Start the server

```bash
uvicorn statmon_mcp.server:app --host 0.0.0.0 --port 8100
```

The server exposes:
- `GET /mcp` — SSE endpoint for MCP client connections
- `POST /messages/` — MCP message handling
- `GET /health` — Health check

### 4. Verify

```bash
curl http://localhost:8100/health
```

Expected: `{"status":"ok","node":"dns-node-a","tools":["statmon"]}`

### Using with mock CLI

To use the mock CLI instead of a real Statmon binary, make it executable and set `NODE_NAME`:

```bash
chmod +x mock-cli/statmon
export NODE_NAME=dns-node-a
```

Then ensure the config points `binary` at `./mock-cli/statmon` (or the absolute path).

## Development

```bash
pytest                        # Run tests
black .                       # Format code
flake8                        # Lint code
```

---

**Last Updated:** February 2026

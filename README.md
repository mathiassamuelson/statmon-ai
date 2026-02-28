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

## Development

```bash
pytest                        # Run tests
black .                       # Format code
flake8                        # Lint code
```

---

**Last Updated:** February 2026

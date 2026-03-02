# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

StatMon AI Aggregator — a natural-language chatbot that enables carrier engineers to query across multiple CacheServe DNS servers and co-located Statmon log collectors from a single interface. Uses MCP (Model Context Protocol) to expose CLI tools on each DNS node to an Anthropic-powered chat application.

## Commands

### Environment Setup
```bash
./setup.sh                              # Full environment setup (creates ~/statmon-ai venv)
source ~/statmon-ai/bin/activate        # Activate the virtual environment
```

### Docker Development
```bash
docker compose up                       # Full stack (configs from host /etc paths)
docker compose up --build               # Rebuild and run
```

### Development Tools
```bash
pytest                        # Run tests
black .                       # Format code
flake8                        # Lint code
```

## Architecture

### Components
- `statmon-mcp/` — MCP server running on each DNS node; exposes `cacheserve` and `statmon` CLI tools with allow/deny command filtering
- `statmon-chat/` — FastAPI web app; connects to all MCP nodes, routes tool calls, mediates with Anthropic API
- `docs/` — Design specification and command references
- `configs/` — Configuration file templates

### Key Design Decisions
- MCP tool names are prefixed with node name (e.g., `dns_node_a__statmon`) for multi-node routing
- Command filtering uses deny-first, then allow-list, then default-deny
- System prompt includes full CLI reference documentation for CacheServe and Statmon commands
- CLI execution uses `nom-tell` with subsystem and key=value arg syntax (e.g., `nom-tell statmon querystore.top-clients duration=3600`)
- `shlex.split()` is used for command parsing to handle S-expression filters with spaces/parentheses

### Configuration
Config files are loaded in order: env var → `~/.config/<component>/config.yaml` → `/etc/<component>/config.yaml`
- MCP server: `STATMON_MCP_CONFIG` env var, or `~/.config/statmon-mcp/config.yaml`, or `/etc/statmon-mcp/config.yaml`
- Chat app: `STATMON_CHAT_CONFIG` env var, or `~/.config/statmon-chat/config.yaml`, or `/etc/statmon-chat/config.yaml`
- Example configs in `configs/mcp-server.example.yaml` and `configs/chat-app.example.yaml`

## Key Files

- `statmon-chat/statmon_chat/prompt.txt` — System prompt template (the authoritative CLI reference + investigation patterns)
- `statmon-chat/statmon_chat/system_prompt.py` — Loads prompt.txt and injects dynamic node list via `{nodes_section}`
- `statmon-mcp/statmon_mcp/cli_executor.py` — Subprocess execution (binary + optional subsystem + command)
- `statmon-mcp/statmon_mcp/filter.py` — Deny/allow command filtering with glob matching
- `docs/StatmonExplainer.md` — Practical troubleshooting examples (source of truth for real-world patterns)
- `docs/statmon-prompt.txt` — Standalone CLI reference (keep in sync with prompt.txt)

## Deployment
- MCP servers run on production DNS nodes inside a Linode VPC (private network)
- Chat app runs locally on macOS, connecting to remote MCP servers
- Chat UI renders assistant responses as markdown (marked.js)

## Key Dependencies

Core: anthropic, mcp, fastapi, uvicorn, pyyaml
Development: pytest, black, flake8

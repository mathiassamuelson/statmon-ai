# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

StatMon AI Aggregator — a natural-language chatbot that enables carrier engineers to query across multiple CacheServe DNS servers and co-located Statmon log collectors from a single interface. Connects to MCP (Model Context Protocol) servers running on each DNS node and mediates between the user and the Anthropic API.

The MCP server lives in a separate repository: [cli-mcp-server](https://github.com/mathiassamuelson/cli-mcp-server).

## Commands

### Environment Setup
```bash
./setup.sh                              # Full environment setup (creates .venv)
source .venv/bin/activate               # Activate the virtual environment
```

### Development Tools
```bash
pytest                        # Run tests
black .                       # Format code
flake8                        # Lint code
```

## Architecture

### Components
- `copilot/` — FastAPI web app; connects to all MCP nodes, dispatches agent-side investigation tools (whois, dns_resolve, ip_geolocation, reverse_dns_lookup), and mediates with the Anthropic API
- `docs/` — Design specification and command references
- `configs/` — Configuration file templates

### Key Design Decisions
- MCP tool names are prefixed with node name (e.g., `dns_node_a__statmon`) for multi-node routing
- Agent-side tools (whois, DNS, geolocation) run locally in the chat app — no MCP server needed for them
- Tool descriptions are loaded from standalone files (`copilot/descriptions/*.md`) at registration time
- System prompt focuses on cross-tool orchestration; per-tool syntax lives in tool descriptions

### Configuration
Config files are loaded in order: env var → `~/.config/copilot/config.yaml` → `/etc/copilot/config.yaml`
- Chat app: `COPILOT_CONFIG` env var, or `~/.config/copilot/config.yaml`, or `/etc/copilot/config.yaml`
- Example config in `configs/chat-app.example.yaml`

## Key Files

- `copilot/copilot/prompt.txt` — System prompt template (orchestration; per-tool syntax is in description files)
- `copilot/copilot/system_prompt.py` — Loads prompt.txt and injects dynamic node list via `{nodes_section}`
- `copilot/copilot/security_tools.py` — Agent-side investigation tools (whois, dns_resolve, ip_geolocation, reverse_dns_lookup)
- `copilot/copilot/descriptions/*.md` — Per-tool description files loaded at registration
- `copilot/copilot/mcp_pool.py` — MCP client pool managing per-node connections
- `copilot/copilot/anthropic_client.py` — Conversation loop, tool dispatch
- `docs/StatmonExplainer.md` — Practical troubleshooting examples (proprietary; not in public repo)
- `docs/statmon-prompt.txt` — Standalone CLI reference (proprietary; not in public repo)

## Deployment
- MCP servers run on production DNS nodes inside a Linode VPC (see [cli-mcp-server](https://github.com/mathiassamuelson/cli-mcp-server) for setup)
- Chat app runs locally on macOS, connecting to remote MCP servers
- Chat UI renders assistant responses as markdown (marked.js)

## Key Dependencies

Core: anthropic, mcp, fastapi, uvicorn, pyyaml, python-whois, dnspython, httpx
Development: pytest, black, flake8

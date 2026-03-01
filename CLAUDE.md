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

## Deployment
- All components run inside a Linode VPC (private network)
- MCP servers listen on private IPs only
- Chat app is the only externally reachable component

## Key Dependencies

Core: anthropic, mcp, fastapi, uvicorn, pyyaml
Development: pytest, black, flake8

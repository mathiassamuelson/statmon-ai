# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project overview

DNS Operator Copilot — a chat application for DNS operators. Connects to MCP servers running on each DNS node (exposing Statmon, CacheServe, Linux diagnostics, etc.), runs local investigation tools (WHOIS, DNS resolution, IP geolocation, reverse DNS), and mediates with the Anthropic API.

The MCP server lives in a separate repository: [cli-mcp-server](https://github.com/mathiassamuelson/cli-mcp-server).

## Commands

### Environment setup

```bash
./setup.sh                              # Create .venv and install in editable mode
source .venv/bin/activate
```

### Development

```bash
pytest                        # Run tests
black .                       # Format
flake8                        # Lint
```

### Run

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export COPILOT_CONFIG=~/.config/copilot/config.yaml
bin/chat-server.sh
```

## Architecture

The chat app composes capabilities from three sources, all of which the agent treats uniformly:

- **Local tools** — Python functions in `copilot/copilot/security_tools.py`. WHOIS, DNS, IP geolocation, reverse DNS. Run in-process.
- **MCP-connected tools** — discovered from each configured MCP server at startup. Tool names are prefixed with the node name (e.g., `dns_node_a__statmon`) so the agent can target specific servers.
- **Anthropic native tools** — currently web search.

Each tool's description is loaded from a standalone Markdown file at registration time (not inlined in code). Local tools read from `copilot/copilot/descriptions/<name>.md`; MCP-side tools use the catalog's `description_file:` field.

The system prompt focuses on cross-tool orchestration. Per-tool syntax lives in the description files.

## Configuration

Config files are loaded in priority order: `COPILOT_CONFIG` env var → `~/.config/copilot/config.yaml` → `/etc/copilot/config.yaml`. See `configs/chat-app.example.yaml` for the schema.

## Key files

- `copilot/copilot/app.py` — FastAPI lifespan and routes
- `copilot/copilot/anthropic_client.py` — Conversation loop, tool dispatch
- `copilot/copilot/mcp_pool.py` — Per-node MCP connections with reconnect logic
- `copilot/copilot/security_tools.py` — Local tool implementations and registration
- `copilot/copilot/system_prompt.py` — Prompt template loader; injects node list into `{nodes_section}`
- `copilot/copilot/prompt.txt` — System prompt template (orchestration; per-tool syntax is in descriptions/)
- `copilot/copilot/descriptions/*.md` — Per-tool description files
- `copilot/copilot/cli.py` — Headless CLI for automated conversations
- `copilot/copilot/trace.py` — Per-request timing and tool-call instrumentation
- `docs/design.md` — Architecture overview

## Deployment

- MCP servers run on DNS nodes — see [cli-mcp-server](https://github.com/mathiassamuelson/cli-mcp-server) for setup.
- Chat app runs locally on macOS/Linux, connecting to remote MCP servers over the network.
- Chat UI renders assistant responses as Markdown (marked.js).

## Key dependencies

Core: anthropic, mcp, fastapi, uvicorn, pyyaml, python-whois, dnspython, httpx
Development: pytest, black, flake8

# DNS Operator Copilot

A chat application that helps DNS operators investigate query traffic, performance issues, and security incidents on production DNS infrastructure. The operator asks questions in natural language; the application composes answers by calling tools across multiple sources.

See [docs/design.md](docs/design.md) for the architecture overview.

## What it does

DNS Operator Copilot connects to:

- **MCP servers running on each DNS node** — exposes whatever CLIs the operator has wrapped (Statmon for query-log analysis, CacheServe for server state, Linux diagnostics for the surrounding host). The MCP server lives in a separate project: [cli-mcp-server](https://github.com/mathiassamuelson/cli-mcp-server).
- **Local investigation tools** — WHOIS, DNS resolution, IP geolocation, and reverse DNS, run as in-process Python functions.
- **Web search** — via the Anthropic API's native web search tool.

The agent treats all three as equivalent tool sources. From the operator's perspective, it's one chat box.

## Quick start

```bash
git clone https://github.com/mathiassamuelson/dns-operator-copilot.git
cd dns-operator-copilot

./setup.sh

export ANTHROPIC_API_KEY=sk-ant-...
export COPILOT_CONFIG=~/.config/copilot/config.yaml
bin/chat-server.sh
```

Then open `http://127.0.0.1:8443`.

Override the bind defaults with `HOST` and `PORT`:

```bash
HOST=0.0.0.0 PORT=9000 bin/chat-server.sh
```

## Setup

### Install dependencies

```bash
./setup.sh
source .venv/bin/activate
```

Or manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ./copilot
```

### Configure

The chat app looks for its config at, in order:

1. `COPILOT_CONFIG` environment variable (full path)
2. `~/.config/copilot/config.yaml`
3. `/etc/copilot/config.yaml`

Copy `configs/chat-app.example.yaml` to one of those paths and edit. The required fields are an Anthropic model and a list of MCP nodes; everything else has sensible defaults.

### Deploy the MCP nodes

For each DNS node you want the copilot to see, deploy [cli-mcp-server](https://github.com/mathiassamuelson/cli-mcp-server) and configure its catalog. The chat app discovers the tools each node exposes at startup; just list the node URLs in `nodes:` in your config.

## Helper scripts

- `bin/chat-server.sh` — start the web UI on `127.0.0.1:8443`. Override with `HOST`/`PORT`.
- `bin/chat-cli.sh` — drive an automated conversation, useful for capturing training data or running scripted investigations.

## Repository structure

```
dns-operator-copilot/
├── copilot/                # The chat application
│   ├── copilot/            # Python package
│   │   ├── app.py
│   │   ├── anthropic_client.py
│   │   ├── mcp_pool.py
│   │   ├── security_tools.py
│   │   ├── descriptions/   # Per-tool description files
│   │   ├── prompt.txt      # System prompt template
│   │   └── templates/
│   ├── pyproject.toml
│   └── Dockerfile
├── bin/                    # Helper scripts
├── configs/                # Configuration templates
├── docs/
│   └── design.md
└── tests/
```

## Customizing the tool catalog on a DNS node

The interesting CLIs your DNS operators care about are deployment-specific — Statmon syntax differs by version, your local diagnostic wrappers aren't anyone else's, and so on. Those tool definitions live in the MCP server's catalog (see [cli-mcp-server's documentation](https://github.com/mathiassamuelson/cli-mcp-server)), not in this repository. The chat app's job is to connect, discover what's there, and surface it to the model.

## Customizing local investigation tools

The four local tools (`whois_lookup`, `dns_resolve`, `ip_geolocation`, `reverse_dns_lookup`) live in `copilot/copilot/security_tools.py`. Their descriptions — what the agent reads when deciding *when* and *how* to use each tool — live in `copilot/copilot/descriptions/`. To improve how the agent uses one of these tools, edit the description file.

To add a new local tool, add a schema to `_TOOL_SCHEMAS`, an async handler, an entry in `_DISPATCH`, and a corresponding `<tool_name>.md` description file. The file is loaded at module import time; missing files raise immediately rather than silently shipping a tool without documentation.

## Development

```bash
pytest                # run the test suite
black .               # format
flake8                # lint
```

## Future direction

The longer arc of this project is to replace the Anthropic API with a locally fine-tuned model — same agent loop, same tools, but no external API call. Training-data capture is already supported via `bin/chat-cli.sh`, which produces multi-turn JSONL covering tool calls and results.

#!/usr/bin/env bash
# Migrate an old single-tool statmon-mcp config.yaml (top-level `statmon:` block)
# into the catalog model.
#
# Usage:
#   migrate-to-catalog.sh <old-config.yaml> <output-dir>
#
# Produces, under <output-dir>:
#   config.yaml                         — new server config with a catalog: block
#   catalog/statmon.yaml                — the migrated tool entry
#   catalog/descriptions/statmon.md     — placeholder description
#
# After running, install <output-dir>/config.yaml to /etc/statmon-mcp/config.yaml
# (or wherever your STATMON_MCP_CONFIG points) and copy <output-dir>/catalog/
# into the path referenced from the new config.

set -euo pipefail

if [ $# -ne 2 ]; then
    echo "usage: $0 <old-config.yaml> <output-dir>" >&2
    exit 2
fi

OLD_CONFIG="$1"
OUT_DIR="$2"

if [ ! -f "$OLD_CONFIG" ]; then
    echo "error: $OLD_CONFIG not found" >&2
    exit 1
fi

mkdir -p "$OUT_DIR/catalog/descriptions"

python3 - "$OLD_CONFIG" "$OUT_DIR" <<'PY'
import sys, yaml
from pathlib import Path

old_path, out_dir = sys.argv[1], Path(sys.argv[2])
with open(old_path) as f:
    old = yaml.safe_load(f)

server = old.get("server") or {}
statmon = old.get("statmon") or {}
if not statmon:
    print("warning: no top-level 'statmon:' block in old config; "
          "writing a fresh server config and a default statmon entry",
          file=sys.stderr)

binary = statmon.get("binary", "/usr/local/nom/sbin/nom-tell")
subsystem = statmon.get("subsystem", "statmon")
timeout = statmon.get("timeout_seconds", 60)
rules = statmon.get("rules") or {
    "deny": ["querystore.reset"],
    "allow": ["querystore.*", "auth-querystore.*"],
}

new_config = {
    "server": {
        "host": server.get("host", "0.0.0.0"),
        "port": server.get("port", 8100),
        "node_name": server.get("node_name", "dns-node"),
    },
    "catalog": {
        "path": "/etc/statmon-mcp/catalog/",
        "search_paths": [
            "/usr/local/sbin", "/usr/local/bin",
            "/usr/sbin", "/usr/bin",
            "/sbin", "/bin",
            "/usr/local/nom/sbin",
        ],
        "defaults": {
            "timeout_seconds": 30,
            "output": {"max_bytes": 65536},
        },
    },
}

statmon_entry = [{
    "name": "statmon",
    "description_file": "descriptions/statmon.md",
    "binary": binary,
    "prepend_args": [subsystem] if subsystem else [],
    "timeout_seconds": timeout,
    "output": {"max_bytes": 1048576},
    "pipe_stage": False,
    "rules": {
        "deny": rules.get("deny", []),
        "allow": rules.get("allow", []),
    },
}]

(out_dir / "config.yaml").write_text(
    yaml.safe_dump(new_config, sort_keys=False)
)
(out_dir / "catalog" / "statmon.yaml").write_text(
    yaml.safe_dump(statmon_entry, sort_keys=False)
)
(out_dir / "catalog" / "descriptions" / "statmon.md").write_text(
    "Execute a read-only Statmon querystore command on this DNS node. "
    "Returns JSON output from the Statmon log collector.\n\n"
    "Replace this file with your site's Statmon CLI reference — "
    "querystore.* and auth-querystore.* command families, key=value "
    "argument syntax, S-expression filter syntax, and group-by "
    "attributes.\n"
)

print(f"wrote {out_dir}/config.yaml")
print(f"wrote {out_dir}/catalog/statmon.yaml")
print(f"wrote {out_dir}/catalog/descriptions/statmon.md")
PY

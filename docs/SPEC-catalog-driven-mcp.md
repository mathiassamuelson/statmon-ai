# Spec: Catalog-Driven Tool Expansion for `statmon-mcp`

**Status:** Draft for implementation
**Scope:** Step 1 — convert the MCP server from a single hardcoded `statmon` tool into a YAML-catalog-driven tool registry, migrate `statmon` into the catalog, and ship a v1 catalog covering read-only Linux sysadmin/SRE commands across Ubuntu and Red Hat-derived distributions.
**Out of scope (this step):** Non-root / sudoers privilege model, CacheServe tool, dynamic catalog reload, per-tool authentication.

---

## 1. Background

Today `statmon-mcp/statmon_mcp/server.py` declares a single tool literally:

```python
@mcp_server.list_tools()
async def list_tools():
    return [Tool(name="statmon", description="...", inputSchema={...})]
```

with execution config (`binary`, `subsystem`, `timeout_seconds`, `rules`) read from a YAML `statmon:` block in `/etc/statmon-mcp/config.yaml`. This works for one tool. It does not scale to the dozens of Linux diagnostic commands we want to expose. This spec replaces the hardcoded path with a catalog system.

The existing safety primitives are kept and generalized:

- `filter.check_command()` — deny-first, allow, default-deny — applied per-tool.
- `cli_executor.run_cli()` — `subprocess_exec` with `shlex.split()`, no shell — extended for pipelines, output capping, proper kill-on-timeout, and sanitized subprocess environment.

---

## 2. High-Level Architecture

After this change:

- **Catalog directory** (`/etc/statmon-mcp/catalog/*.yaml`) declares tools as data. Each YAML file contains one or more tool entries. The server discovers, validates, and registers them at startup.
- **`server.py`** loads the catalog and registers one MCP `Tool` per entry. `list_tools()` returns the registered list. `call_tool()` dispatches by tool name to the catalog entry's executor configuration.
- **Binary resolution** at startup uses a configurable `search_paths` list rather than the process's inherited `PATH`. Catalog entries name their binary as a bare name (`ip`, `journalctl`) or absolute path (`/usr/local/nom/sbin/nom-tell`); the loader resolves bare names against `search_paths`. This is portable across Ubuntu/RHEL path differences and immune to environment leaks.
- **Pipeline support** via a sandboxed grammar: a tool's `command` argument may contain `|` to chain catalog tools that have been flagged as `pipe_stage: true`. Each segment is filtered independently; no shell is invoked.
- **`filter.py`** is unchanged in semantics. It is now invoked per-tool with that tool's rules.
- **`cli_executor.py`** gains: hard kill on timeout, streaming output cap, pipeline execution, and a sanitized `env=` passed to every subprocess.

The MCP wire protocol is unchanged. Each catalog tool becomes one `Tool` advertised via `tools/list`. Existing chat-side prefixing (`<node>__<tool>`) continues to work.

---

## 3. Catalog Design

### 3.1 Directory layout

```
/etc/statmon-mcp/
├── config.yaml                       # server config (host, port, node_name, catalog defaults, search_paths)
└── catalog/
    ├── statmon.yaml
    ├── linux-process.yaml
    ├── linux-fs.yaml
    ├── linux-disk.yaml
    ├── linux-net.yaml
    ├── linux-systemd.yaml
    ├── linux-logs.yaml
    ├── linux-packages-deb.yaml       # apt, apt-cache, dpkg, snap (Ubuntu/Debian)
    ├── linux-packages-rpm.yaml       # rpm, dnf/yum (RHEL/Fedora/Rocky/Alma)
    ├── linux-system.yaml
    ├── linux-text.yaml               # pipe-stage text processors
    ├── linux-containers.yaml
    ├── linux-kernel.yaml
    ├── linux-selinux.yaml            # RHEL-relevant; unhealthy on Ubuntu hosts without SELinux
    └── descriptions/
        └── statmon.md                # long-form tool descriptions referenced by description_file
```

The catalog directory path is configurable in `config.yaml` (default `/etc/statmon-mcp/catalog/`). The server reads every `*.yaml` file in that directory at startup; subdirectories other than `descriptions/` are ignored.

The same catalog ships to both Ubuntu and RHEL hosts. Tools whose binaries aren't present register as unhealthy and return a clear error envelope when called; no conditional loading or per-distro builds are needed.

### 3.2 Tool entry schema

A YAML file in `catalog/` contains a top-level list of tool entries:

```yaml
- name: <string>                  # required; MCP tool name; must be unique across catalog
  description: <string>           # one of {description, description_file} required
  description_file: <path>        # path relative to catalog dir; loaded as plain text
  binary: <string>                # required; either an absolute path or a bare name resolved via search_paths
  prepend_args: [<string>, ...]   # optional; fixed args inserted before the model's command (default: [])
  timeout_seconds: <int>          # required (or via defaults); hard kill after this many seconds
  output:
    max_bytes: <int>              # default 65536; truncate stdout beyond this
  pipe_stage: <bool>              # default false; if true, may appear as a non-lead pipeline segment
  rules:
    deny: [<glob>, ...]           # optional; matched first
    allow: [<glob>, ...]          # optional; required for any command to pass
  path_rules:                     # optional; convenience deny patterns checked separately
    deny: [<glob>, ...]           # matched against any token that looks like a path (starts with /, ./, or ../)
```

**Rules:**

- `name` must be unique across all catalog files. A duplicate is a startup error.
- Exactly one of `description` and `description_file` must be present. `description_file` is resolved relative to the catalog directory.
- `binary` may be absolute (used as-is) or a bare name (resolved against `catalog.search_paths` at startup; first executable match wins). On resolution failure the tool is registered with an "unhealthy" marker that returns an error envelope on call.
- `prepend_args` is a list of strings spliced in **before** the model's parsed command tokens, after `binary`. Used by Command Channel-style binaries where one driver fronts multiple applications (see §8). Default is empty list.
- An empty or missing `allow` combined with no `deny` is **not** permitted (would default-deny everything). The server rejects such entries at startup.
- `pipe_stage: true` does **not** prevent the tool from being called as a lead command — it just permits use as a downstream pipeline segment.

### 3.3 Loading and validation

At startup the server:

1. Reads `config.yaml`. Resolves `catalog.path` (default `/etc/statmon-mcp/catalog/`) and `catalog.search_paths` (default list, see §3.4).
2. Iterates `*.yaml` in the catalog directory in lexicographic order.
3. Parses each as a list of entries. Validates each entry against the schema (required fields, types, exclusive `description`/`description_file`, non-empty rules).
4. Resolves `description_file` references and inlines them into the entry's effective description.
5. Resolves `binary`:
   - Absolute path: verify it exists and is executable (`os.access(path, os.X_OK)`).
   - Bare name: iterate `search_paths` and pick the first entry where `<dir>/<binary>` is executable.
   - Failure: log a structured warning identifying the entry and missing binary; mark the tool unhealthy. Do not exit — production hosts may legitimately lack some tools, and an Ubuntu host won't have `dnf` while an RHEL host won't have `apt`.
6. Caches the resolved absolute path on the `ToolEntry`. The executor never re-resolves and never consults `os.environ["PATH"]`.
7. Detects duplicate `name` across files. Hard error; server exits with a clear message.
8. Builds an in-memory `ToolRegistry` mapping `name -> ToolEntry`.

Validation errors include the file path and entry index/name in the error message so operators can fix them quickly.

### 3.4 Per-tool defaults and search paths

`config.yaml` carries catalog-wide defaults that individual entries may override, plus the binary search path list:

```yaml
catalog:
  path: /etc/statmon-mcp/catalog/
  search_paths:
    - /usr/local/sbin
    - /usr/local/bin
    - /usr/sbin
    - /usr/bin
    - /sbin
    - /bin
    - /usr/local/nom/sbin     # nom-tell lives here
  defaults:
    timeout_seconds: 30
    output:
      max_bytes: 65536
```

Entry values take precedence over defaults. The `statmon` entry will override `timeout_seconds: 60` and `output.max_bytes: 1048576` to keep current behavior.

The default search paths cover Ubuntu and RHEL family path layouts (modern usrmerge-era and pre-merge). Operators can extend the list for site-specific binary locations without changing catalog YAML.

---

## 4. Server Changes (`server.py`)

### 4.1 Dynamic registration

Replace the hardcoded `list_tools()` body with a loop over `ToolRegistry`. Each entry produces:

```python
Tool(
    name=entry.name,
    description=entry.effective_description,   # inlined from description or description_file
    inputSchema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    "Arguments to pass to the tool. May include pipes to other catalog "
                    "tools flagged as pipe stages (e.g., `aux | grep nginx | head -5`)."
                ),
            }
        },
        "required": ["command"],
    },
)
```

The schema is identical for every tool. The model-facing differentiation lives in `description`.

### 4.2 Dispatch

`call_tool(name, arguments)`:

1. Look up `entry = registry[name]`. Unknown tool → return error envelope (current behavior).
2. If entry is unhealthy (binary missing) → return error envelope identifying which binary is missing and which `search_paths` were consulted.
3. Parse the command into a pipeline (see §5). The lead segment is constrained to `entry`. Each non-lead segment is resolved against the registry; any segment whose tool is missing or whose `pipe_stage` is false → return error envelope.
4. For each segment, run `check_command(segment_args, segment_entry.rules)` and any `path_rules.deny` checks. First denial → return denied envelope identifying which segment was denied and why.
5. Hand off to the executor (§6) with the resolved pipeline.
6. Wrap the result in the existing envelope shape, with a per-segment `pipeline` array if the pipeline length is > 1:

```json
{
  "node": "dns-node-a",
  "tool": "ps",
  "command": "aux | grep nginx | head -5",
  "pipeline": [
    {"tool": "ps", "args": "aux"},
    {"tool": "grep", "args": "nginx"},
    {"tool": "head", "args": "-5"}
  ],
  "status": "success",
  "exit_code": 0,
  "execution_time_ms": 42,
  "result": "...stdout text..."
}
```

The single-segment case omits the `pipeline` field for backward compatibility with `statmon` consumers.

---

## 5. Pipeline Grammar

### 5.1 Syntax

A command string is a sequence of segments separated by literal `|` characters that appear **outside** any quoted string. Each segment is `<tool-name> <args...>` for non-lead segments, and just `<args...>` for the lead segment (the lead tool is the one being called via MCP).

Examples (lead tool in brackets):

- `[ps] aux | grep nginx | head -5`
- `[journalctl] -u nginx --since "1 hour ago" | grep -i error | tail -50`
- `[ss] -tnp | awk '$1=="ESTAB"' | wc -l`

### 5.2 Parser rules

- Tokenize via a single-pass scanner that respects single and double quotes (so `|` inside `'…'` or `"…"` is not a separator).
- Reject any of these unquoted metacharacters anywhere in the command: `;`, `&`, `>`, `<`, `` ` ``, `$(`, `&&`, `||`, newline. These produce a `denied` envelope with reason `pipeline grammar: forbidden metacharacter '<x>'`.
- Globs (`*`, `?`, `[…]`) are allowed in arguments; they are passed verbatim to the binary, which expands them itself or not. We do not do shell-style glob expansion.
- Empty segments (`ps aux | | head`) are an error.

### 5.3 Pipe-stage allowlist

Only catalog tools with `pipe_stage: true` may appear in non-lead position. Initial pipe-stage set (defined in `linux-text.yaml`):

`grep`, `egrep`, `fgrep`, `awk`, `sed`, `head`, `tail`, `wc`, `sort`, `uniq`, `cut`, `tr`, `cat`, `tac`, `rev`, `column`, `nl`, `fold`, `expand`, `unexpand`, `xxd`, `od`, `strings`

Lead-only tools (everything else) cannot appear in non-lead position. This bounds what the pipeline can do: text in, text out, no side effects, no fan-out.

### 5.4 Validation order

For a pipeline with N segments:

1. Grammar parse (§5.2). Failure → grammar error.
2. Resolve each segment to a registry entry. Missing tool, or non-lead use of a non-`pipe_stage` tool → resolution error.
3. Per-segment filter check (deny → allow → default-deny against `entry.rules`). First denial → denied envelope identifying the segment.
4. Per-segment `path_rules.deny` check on tokens that begin with `/` or `./` or `../`. First match → denied.
5. Execute (§6.3).

---

## 6. Executor Changes (`cli_executor.py`)

### 6.1 Kill on timeout

Today's `asyncio.wait_for(proc.communicate(), timeout=...)` cancels the wait but leaves the subprocess alive. Required change:

- On `asyncio.TimeoutError`, call `proc.kill()`, then `await proc.wait()` to reap it.
- On any other exception path that leaves the process running, ensure the same cleanup.

This matters for tools that can run unbounded (`tcpdump`, `journalctl --since`, `find /`).

### 6.2 Output cap (streaming)

Replace `proc.communicate()` with a streaming reader that:

- Reads stdout in chunks until either EOF, `max_bytes` reached, or timeout.
- On `max_bytes` reached, kills the subprocess and appends `\n[output truncated at <N> bytes]` to the captured output.
- Reads stderr in parallel with a smaller cap (e.g., 8 KB) — error messages should be short.

Without this, `find /` floods memory before the chat-side 15 KB truncation ever runs.

### 6.3 Pipeline execution

Build the subprocess chain manually (no shell). For a pipeline `t1 args1 | t2 args2 | t3 args3`:

```
t1: stdin=DEVNULL, stdout=PIPE
t2: stdin=t1.stdout, stdout=PIPE
t3: stdin=t2.stdout, stdout=PIPE   ← captured & capped
```

Each subprocess uses its catalog entry's resolved `binary` plus that entry's `prepend_args` plus `shlex.split(args)`.

Behavior:

- Apply the **lead** entry's `timeout_seconds` to the whole pipeline. (Operators tune the lead tool's timeout knowing pipelines may chain off it.)
- Apply the **last** entry's `output.max_bytes` to the captured stdout.
- On timeout or output cap, kill all subprocesses in the chain.
- Result `exit_code` is the last segment's exit code. If any non-last segment exited non-zero AND the last segment's exit code is 0, surface a `warning` field in the envelope listing the non-zero non-last segments — do not flip overall status to error (mirrors shell behavior with `pipefail` off, which matches what most SREs expect from CLI output).

### 6.4 Subprocess environment

Every subprocess receives a sanitized `env=` rather than inheriting the server's environment:

```python
{
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}
```

Rationale:

- **Stable output.** Many tools change format based on locale (`ls`, `date`, `df`, `journalctl`). `LANG=C` / `LC_ALL=C` produces deterministic English output the model can rely on.
- **Defense in depth.** Tools that re-invoke other binaries (e.g., `iostat` or `mtr` shelling to other utilities) use this sanitized `PATH` rather than whatever leaked in via the server's environment.
- **Future consistency.** When we move to non-root + sudoers, sudoers' `env_reset` would force this anyway. Doing it now keeps subprocess behavior identical across privilege models.

A future per-entry `env:` override hook can be added if a specific tool needs `LD_LIBRARY_PATH` or similar; not required v1, and the executor should be structured so the override is a small change rather than a refactor.

### 6.5 Result shape

Single-segment results match today's envelope. Multi-segment results add the `pipeline` array (§4.2) and may add `warning`. JSON parsing of `result` is attempted only when the pipeline length is 1 and the lead tool's output is plausibly JSON; otherwise `result` is the captured stdout string. (This preserves statmon JSON behavior without trying to second-guess what `ps aux | grep` should return.)

---

## 7. Filter Changes (`filter.py`)

The matching logic does not change. The integration changes:

- `check_command` is invoked per-segment with that segment's rules.
- Add a `check_paths(args: str, deny: list[str]) -> tuple[bool, str]` helper that tokenizes args via `shlex.split`, picks tokens beginning with `/`, `./`, or `../`, and tests each against the deny patterns. Returns `(False, "path matches deny rule: <pattern>")` on first match.
- Update tests in `tests/test_filter.py` to cover both the existing semantics and the new path-rules helper.

---

## 8. `statmon` Migration

### 8.1 Background on `nom-tell`

`statmon` and CacheServe are Akamai applications that expose a proprietary RPC interface called **Command Channel (CC)**. `nom-tell` is the CC client binary — a generic driver that talks to any CC-speaking application. When invoked as `nom-tell <alias> <command...>`, `nom-tell` looks up `<alias>` in `/etc/channel.conf` to obtain the target application's IP address, port, and shared secret, then executes the command over CC.

This means:

- The MCP tool name (`statmon`) is independent of the binary on disk (`nom-tell`).
- Authentication and transport are handled entirely inside `nom-tell` via `/etc/channel.conf` — no secrets surface in catalog YAML.
- Future CC-speaking applications (CacheServe and others) will be additional catalog entries that share `binary: nom-tell` but use different `prepend_args`.

This is exactly the use case `prepend_args` exists for. The list `["statmon"]` is the CC channel alias that gets spliced in between `nom-tell` and the model's command.

### 8.2 Catalog entry

The current `statmon:` block in `config.yaml` becomes `catalog/statmon.yaml`:

```yaml
- name: statmon
  description_file: descriptions/statmon.md
  binary: /usr/local/nom/sbin/nom-tell    # absolute path; lives outside default search_paths
  prepend_args: ["statmon"]               # CC channel alias resolved by nom-tell via /etc/channel.conf
  timeout_seconds: 60
  output:
    max_bytes: 1048576                    # statmon results can be large; allow 1 MB
  pipe_stage: false
  rules:
    deny:
      - "querystore.reset"
    allow:
      - "querystore.*"
      - "auth-querystore.*"
```

`nom-tell` could alternatively be added to `search_paths` (the default list already includes `/usr/local/nom/sbin`); the absolute `binary:` is shown here for explicitness on what is a non-standard install location.

### 8.3 Description content

`descriptions/statmon.md` holds the existing CLI reference content from `prompt.txt` covering:

- Common arguments (duration, end, interval, filter, max-results, source, anonymize)
- Activity commands (top/bottom-clients, top/bottom-domains, top/bottom-views and their bandwidth variants)
- Aggregate metrics (count, qps, request-bandwidth, response-bandwidth, *-per-second)
- Grouping (group-count, group-count-size, replay)
- System (status, reset)
- Group-by attribute list with the parenthesized-tuple syntax note
- Filter syntax (S-expressions), result-code values, flags values, examples
- Auth-querystore section (commands, group-by attributes, queryfilter attributes, examples)

The system prompt's authoritative source for statmon CLI syntax shifts from `prompt.txt` to this file. `prompt.txt` retains:

- Available Nodes (dynamic)
- Tool Usage Guidelines (parallel querying, duration advice, core-domain vs domain)
- Investigation Patterns (Health Check, SERVFAIL, DDoS/PRSD, Amplification, Performance, Malware/C2, Forensic Replay)
- Domain & IP Investigation Tools section (whois, dns_resolve, ip_geolocation, reverse_dns_lookup, web search) — these are security tools dispatched chat-side, not catalog entries
- SecOps Investigation Patterns

The CacheServe section in `prompt.txt` can be removed; CacheServe will arrive as its own catalog file when the real CLI reference lands.

### 8.4 Migration verification

Existing statmon end-to-end tests must pass without modification once the catalog entry is in place. The on-the-wire MCP behavior for the `statmon` tool is unchanged — same name, same input schema, same envelope shape.

---

## 9. Linux v1 Catalog (Full Sysadmin Sweep)

### 9.0 Conventions

The v1 catalog targets **Ubuntu (and Debian derivatives) and Red Hat Enterprise Linux (and derivatives — Fedora, Rocky, AlmaLinux, CentOS Stream)**. The same catalog ships to both; binaries that aren't installed register as unhealthy and return a clear error envelope on call.

**Binary references use bare names.** Every entry below specifies `binary` as a bare name (e.g., `ip`, `journalctl`). The loader resolves these against `catalog.search_paths` (§3.4), which is preconfigured to cover both Ubuntu and RHEL path layouts (`/usr/sbin`, `/sbin`, `/usr/bin`, `/bin`, plus the `usr`-merged equivalents and `/usr/local/*`). Operators don't need to know which distro puts `ip` in `/usr/sbin` versus `/sbin`.

**Two filter idioms are used:**

- **Verb-based tools** (subcommand at position 1): empty `deny`, allow-list of read verbs, default-deny catches everything else. Examples: `systemctl`, `apt`, `dnf`, `dpkg`, `rpm`, `docker`, `kubectl`.
- **Flag-based tools** (most things): `deny` lists destructive flags, `allow: ["*"]` permits everything else.

**Distro availability.** Most v1 tools are present on both families. Where a tool is family-specific (e.g., `snap` on Ubuntu, `getenforce` on RHEL), it's noted; the catalog still ships everywhere and the unhealthy-marker handles the absence gracefully.

### 9.1 Process & runtime — `linux-process.yaml`

| name | binary | filter idiom | notes |
|---|---|---|---|
| `ps` | `ps` | flag-based, no deny | no destructive flags |
| `pgrep` | `pgrep` | flag-based, deny `*--signal*` | prevent signal sending |
| `pidof` | `pidof` | flag-based, no deny | |
| `pstree` | `pstree` | flag-based, no deny | optional package on RHEL minimal (`psmisc`) |
| `top` | `top` | flag-based; allow-list curated (require batch + count) | see allow list below |
| `lsof` | `lsof` | flag-based, no deny | optional package on RHEL minimal |

`top` allow-list:
```yaml
allow:
  - "-b -n 1*"
  - "-b -n *"
  - "-bn 1*"
  - "-bn *"
```
Document in description that `-b -n N` is required (batch mode + iteration count).

### 9.2 Filesystem — `linux-fs.yaml`

| name | binary | deny patterns |
|---|---|---|
| `ls` | `ls` | none |
| `stat` | `stat` | none |
| `du` | `du` | none |
| `df` | `df` | none |
| `file` | `file` | none |
| `find` | `find` | `*-delete*`, `*-exec *`, `*-execdir *`, `*-ok *`, `*-okdir *`, `*-fprint*`, `*-fls*` |
| `readlink` | `readlink` | none |
| `realpath` | `realpath` | none |
| `mount` | `mount` | allow-list only (see below) |
| `findmnt` | `findmnt` | none |
| `tree` | `tree` | none (optional package on both families) |

`mount` is the awkward one — same binary does both info and side-effecty operations:
```yaml
allow:
  - ""             # bare invocation: lists all mounts
  - "-l*"          # listing variants
  - "-t *"         # type filter
  - "--show-labels*"
deny:
  - "*--bind*"
  - "*--move*"
  - "*--remount*"
```
Default-deny catches `mount /dev/sda1 /mnt`.

### 9.3 Disk & block — `linux-disk.yaml`

| name | binary | deny patterns | notes |
|---|---|---|---|
| `lsblk` | `lsblk` | none | |
| `blkid` | `blkid` | none | |
| `smartctl` | `smartctl` | `*-t *`, `*--test*`, `*--abort-test*`, `*-X*` | optional package (`smartmontools`) |
| `iostat` | `iostat` | none | optional package (`sysstat`) |
| `nfsstat` | `nfsstat` | none | optional package (`nfs-common` on Ubuntu, `nfs-utils` on RHEL) |

### 9.4 Network — `linux-net.yaml`

| name | binary | filter idiom | notes |
|---|---|---|---|
| `ip` | `ip` | verb-allow: `addr`, `addr show*`, `a`, `a show*`, `route`, `route show*`, `r`, `r show*`, `link`, `link show*`, `l`, `l show*`, `neigh`, `neigh show*`, `n`, `n show*`, `rule show*`, `tunnel show*`, `-s *`, `-d *`, `-br *`, `-c *` | path differs across distros; search_paths covers both |
| `ss` | `ss` | flag-based, deny `*-K*` (kills sockets) | |
| `netstat` | `netstat` | flag-based, no deny | optional package (`net-tools`) on both families |
| `ping` | `ping` | flag-based, allow only invocations containing `-c` (require count); pattern `*-c *` | |
| `traceroute` | `traceroute` | flag-based, no deny | optional package on both families |
| `mtr` | `mtr` | flag-based, allow only with `-r` (report mode) and `-c` (count) | optional package |
| `dig` | `dig` | none | optional package (`dnsutils` on Ubuntu, `bind-utils` on RHEL) |
| `nslookup` | `nslookup` | none | same package as `dig` |
| `host` | `host` | none | same package as `dig` |
| `arp` | `arp` | flag-based, deny `*-d*`, `*-s*` | optional package (`net-tools`) |
| `tcpdump` | `tcpdump` | flag-based, deny `*-w*`, `*-z*`, `*-W*`, `*-G*`; rely on the lead tool's `timeout_seconds` for hard cap | optional package |
| `nmap` | — | **Deferred to v2.** Even `-sn` ping scans across a subnet can be misused as reconnaissance. | |

Document in `tcpdump`'s description that callers should always include `-c <N>` to bound packet count, and that the executor will hard-kill at `timeout_seconds` regardless.

### 9.5 System & memory — `linux-system.yaml`

| name | binary | deny | notes |
|---|---|---|---|
| `free` | `free` | none | |
| `vmstat` | `vmstat` | none | optional package (`procps` on Ubuntu, `procps-ng` on RHEL — both ship in base) |
| `mpstat` | `mpstat` | none | optional package (`sysstat`) |
| `sar` | `sar` | none (report mode is read-only) | optional package (`sysstat`) |
| `uptime` | `uptime` | none | |
| `w` | `w` | none | |
| `who` | `who` | none | |
| `last` | `last` | none | |
| `uname` | `uname` | none | |
| `hostnamectl` | `hostnamectl` | verb-allow: `""` (bare), `status` | |
| `lscpu` | `lscpu` | none | |
| `lsmem` | `lsmem` | none | |
| `lspci` | `lspci` | none | optional package (`pciutils`) |
| `lsusb` | `lsusb` | none | optional package (`usbutils`) |
| `dmidecode` | `dmidecode` | none (info only) | |
| `nproc` | `nproc` | none | |
| `sysctl` | `sysctl` | flag-based, deny `*-w*`, `*--write*`, `*-p*`; allow `-a`, `-n *`, bare names | |

### 9.6 Logs — `linux-logs.yaml`

| name | binary | deny |
|---|---|---|
| `journalctl` | `journalctl` | `*--rotate*`, `*--vacuum-*`, `*--flush*`, `*--sync*`, `*--relinquish-var*`, `*--update-catalog*`, `*-f*` (no follow — would block forever), `*--follow*` |
| `dmesg` | `dmesg` | `*-C*`, `*--clear*`, `*-c*`, `*--read-clear*` |

Both present on every modern systemd-based distro.

### 9.7 systemd — `linux-systemd.yaml`

`systemctl` uses the verb-allow idiom:

```yaml
- name: systemctl
  binary: systemctl
  timeout_seconds: 15
  rules:
    deny: []
    allow:
      - "status"
      - "status *"
      - "show"
      - "show *"
      - "cat *"
      - "is-active *"
      - "is-enabled *"
      - "is-failed *"
      - "is-system-running"
      - "list-units"
      - "list-units *"
      - "list-unit-files"
      - "list-unit-files *"
      - "list-jobs"
      - "list-dependencies *"
      - "get-default"
      - "list-timers"
      - "list-timers *"
      - "list-sockets"
      - "list-sockets *"
      - "list-machines"
      - "--version"
```

Anything not in the allow list (start, stop, restart, reload, enable, disable, mask, unmask, daemon-reload, edit, kill, set-default, isolate, etc.) is default-denied.

### 9.8 Packages — Debian family — `linux-packages-deb.yaml`

Present on Ubuntu/Debian; unhealthy on RHEL hosts.

`dpkg` (verb-allow):
```yaml
allow:
  - "-l"
  - "-l *"
  - "--list"
  - "--list *"
  - "-L *"
  - "--listfiles *"
  - "-s *"
  - "--status *"
  - "-S *"
  - "--search *"
  - "-p *"
  - "--print-avail *"
  - "-V"
  - "-V *"
  - "--verify"
  - "--verify *"
  - "--print-architecture"
```

`apt` (verb-allow):
```yaml
allow:
  - "list"
  - "list *"
  - "show *"
  - "search *"
  - "depends *"
  - "rdepends *"
  - "policy"
  - "policy *"
  - "moo"
```

`apt-cache` (verb-allow): `search *`, `show *`, `showpkg *`, `depends *`, `rdepends *`, `policy *`, `pkgnames`, `pkgnames *`, `stats`, `dump`, `dumpavail`.

`snap` (verb-allow): `list`, `list *`, `info *`, `find *`, `services`, `connections`, `connections *`, `version`, `model`, `whoami`. Ubuntu-specific (no analog on RHEL).

### 9.9 Packages — RPM family — `linux-packages-rpm.yaml`

Present on RHEL/Fedora/Rocky/Alma; unhealthy on Ubuntu hosts.

`rpm` (verb-allow):
```yaml
allow:
  - "-q*"          # all -q query variants: -qa, -qi, -ql, -qf, -qR, -q --whatprovides, etc.
  - "--query*"
  - "-V"           # verify (read-only check)
  - "-V *"
  - "--verify"
  - "--verify *"
  - "--showrc"
  - "--eval *"     # macro expansion; read-only
```

The `-q*` glob is intentionally broad — every `-q` invocation is a query. `-i` (install) and `-e` (erase) are not in allow and fall to default-deny.

`dnf` (verb-allow):
```yaml
allow:
  - "list"
  - "list *"
  - "info *"
  - "search *"
  - "repolist"
  - "repolist *"
  - "repoquery *"
  - "history"
  - "history list"
  - "history list *"
  - "history info"
  - "history info *"
  - "provides *"
  - "whatprovides *"
  - "check"
  - "check *"
  - "deplist *"
  - "module list"
  - "module list *"
  - "module info *"
  - "--version"
```

`yum` (verb-allow): same as `dnf` (yum is a compatibility wrapper on modern RHEL). Ship as a separate catalog entry pointing at the `yum` binary.

### 9.10 Text processors & pipe stages — `linux-text.yaml`

All of these have `pipe_stage: true`. Most can also be called as a lead tool. Critical safety items:

| name | binary | pipe_stage | special deny |
|---|---|---|---|
| `cat` | `cat` | true | none (path_rules.deny is operator-supplied) |
| `tac` | `tac` | true | none |
| `head` | `head` | true | none |
| `tail` | `tail` | true | `*-f*`, `*--follow*` (no follow) |
| `grep` | `grep` | true | none |
| `egrep` | `egrep` | true | none |
| `fgrep` | `fgrep` | true | none |
| `awk` | `awk` | true | `*system(*`, `*system (*`, `*\| getline*`, `*getline*\|*`, `*"\|&*`, `*"\|"*` (any pipe-to-command construct) |
| `sed` | `sed` | true | `*-i*`, `*--in-place*`, `*e *` (sed `e` command shells out), `*w *` (sed `w` writes to file) |
| `sort` | `sort` | true | `*-o *`, `*--output*` |
| `uniq` | `uniq` | true | none |
| `wc` | `wc` | true | none |
| `cut` | `cut` | true | none |
| `tr` | `tr` | true | none |
| `column` | `column` | true | none |
| `nl` | `nl` | true | none |
| `fold` | `fold` | true | none |
| `xxd` | `xxd` | true | none |
| `od` | `od` | true | none |
| `strings` | `strings` | true | none (optional package on RHEL — `binutils`) |
| `rev` | `rev` | true | none |

The `awk` and `sed` deny patterns are string-glob heuristics, not parsers, so a determined attacker could probably craft a bypass. Acceptable for v1 because:

- The model is the only "attacker" and isn't trying to escape.
- Operator review of catalog rules catches obvious holes.
- The fallback safety is that the executor doesn't use a shell, so even if awk shells out via `system()`, the resulting command runs as the MCP server's process user (root in v1) — which is no worse than what the tool could already do.

If we later need stronger guarantees, we can swap heuristics for AST-level inspection.

### 9.11 Containers — `linux-containers.yaml`

Optional / host-dependent on both distros; ship the YAML entries but expect `binary` checks to mark them unhealthy on hosts without docker/kubectl.

`docker` (verb-allow):
```yaml
allow:
  - "ps"
  - "ps *"
  - "images"
  - "images *"
  - "image ls*"
  - "image inspect *"
  - "container ls*"
  - "container inspect *"
  - "inspect *"
  - "logs *"
  - "version"
  - "info"
  - "system info"
  - "system df"
  - "network ls"
  - "network inspect *"
  - "volume ls"
  - "volume inspect *"
  - "events --since *"
  - "top *"
  - "diff *"
  - "port *"
deny:
  - "*logs*-f*"
  - "*logs*--follow*"
  - "stats"            # bare stats streams; force --no-stream
  - "events"           # bare events streams
```

Document in description: `docker stats` requires `--no-stream`; `docker events` requires `--since`.

`kubectl` (verb-allow):
```yaml
allow:
  - "get *"
  - "describe *"
  - "logs *"
  - "top *"
  - "version"
  - "cluster-info"
  - "cluster-info dump*"
  - "config view*"
  - "config get-contexts*"
  - "config current-context"
  - "explain *"
  - "api-resources"
  - "api-versions"
  - "auth can-i *"
deny:
  - "*logs*-f*"
  - "*logs*--follow*"
  - "*--watch*"
```

Note: `-w` is `--watch` on `kubectl get`, but `-w` is also used by other subcommands for unrelated meaning. The deny pattern targets `--watch` (the long form) to avoid false positives. Document that the model should prefer `--watch` over `-w`.

### 9.12 SELinux — `linux-selinux.yaml`

RHEL-relevant (and any Ubuntu host with SELinux installed, which is rare). Tools ship in the catalog; unhealthy on hosts without them.

| name | binary | filter |
|---|---|---|
| `getenforce` | `getenforce` | none (binary takes no destructive args) |
| `sestatus` | `sestatus` | none |
| `getsebool` | `getsebool` | flag-based, no deny |
| `seinfo` | `seinfo` | flag-based, no deny |
| `sesearch` | `sesearch` | flag-based, no deny |
| `semodule` | `semodule` | verb-allow: `-l`, `-l *`, `--list-modules`, `--list-modules *` |
| `semanage` | `semanage` | verb-allow: `<subject> -l`, `<subject> --list` (where `<subject>` is `boolean`, `port`, `fcontext`, `user`, `login`, `node`, `interface`, `module`, `permissive`) |
| `audit2why` | `audit2why` | none (analyzes audit log; doesn't modify) |

`semanage` allow list (every read variant):
```yaml
allow:
  - "boolean -l*"
  - "boolean --list*"
  - "port -l*"
  - "port --list*"
  - "fcontext -l*"
  - "fcontext --list*"
  - "user -l*"
  - "user --list*"
  - "login -l*"
  - "login --list*"
  - "node -l*"
  - "node --list*"
  - "interface -l*"
  - "interface --list*"
  - "module -l*"
  - "module --list*"
  - "permissive -l*"
  - "permissive --list*"
```

### 9.13 Kernel — `linux-kernel.yaml`

| name | binary | filter |
|---|---|---|
| `lsmod` | `lsmod` | none |
| `modinfo` | `modinfo` | none |

### 9.14 Explicitly excluded from v1

These should **not** be added to the catalog. Document in the spec so future contributors understand the intent.

- **`dd`** — too easy to misuse; no read-only-only invocation that's worth the risk surface.
- **`mkfs.*`, `fdisk`, `parted`, `wipefs`, `cfdisk`, `sfdisk`** — destructive by design.
- **`iptables`, `nft`, `ufw`, `firewalld`, `firewall-cmd`** — even read modes need elevated privileges and most expansion of these would be a write operation; defer to v2.
- **`modprobe`, `insmod`, `rmmod`** — modifies kernel.
- **`shutdown`, `reboot`, `halt`, `poweroff`** — obvious.
- **`crontab`, `at`, `systemd-run`** — schedules code.
- **`useradd`, `usermod`, `userdel`, `groupadd`, `passwd`** — user mutation.
- **`strace`, `ltrace`, `perf`, `bpftrace`** — powerful, side-effecty (perf can write to disk; strace attaching to PIDs has performance impact; bpftrace runs arbitrary code). Defer.
- **`ssh`, `scp`, `rsync`, `curl`, `wget`** — exfiltration vectors and lateral movement. Defer.
- **`vi`, `vim`, `nano`, `emacs`** — interactive; would hang the executor.
- **`docker exec`, `docker run`, `kubectl exec`, `kubectl port-forward`, `kubectl cp`** — explicitly denied in the verb allow-lists above, but worth restating: any container-shell-out is out of scope.
- **`setenforce`, `semanage` (modify subcommands)** — SELinux mutation; only read variants are included.

---

## 10. Configuration File Changes

### 10.1 New `config.yaml` shape

```yaml
server:
  host: "0.0.0.0"
  port: 8100
  node_name: "dns-node-a"

catalog:
  path: "/etc/statmon-mcp/catalog/"
  search_paths:
    - /usr/local/sbin
    - /usr/local/bin
    - /usr/sbin
    - /usr/bin
    - /sbin
    - /bin
    - /usr/local/nom/sbin
  defaults:
    timeout_seconds: 30
    output:
      max_bytes: 65536
```

The old `statmon:` block is **removed** from `config.yaml`. Its content moves to `catalog/statmon.yaml` (§8).

### 10.2 Backward compatibility

Operators upgrading from a single-tool deployment will need to:

1. Update `config.yaml` to the new shape (remove `statmon:` block, add `catalog:` block).
2. Drop `statmon.yaml` into `catalog/`.
3. Drop `descriptions/statmon.md` into `catalog/descriptions/`.

This is a one-time manual migration. Provide a `configs/migrate-to-catalog.sh` script that reads an old config and emits the new files alongside it. Alternative: a startup compatibility shim that detects an old `statmon:` block and synthesizes the catalog entry in-memory with a deprecation log line. Either works; the script is simpler and explicit.

---

## 11. System Prompt Changes

In `statmon-chat/statmon_chat/prompt.txt`:

- **Remove** the entire `Statmon Querystore CLI Reference` section (now lives in `descriptions/statmon.md`, surfaced via the tool description).
- **Remove** the `Authoritative Querystore (auth-querystore)` section (same).
- **Remove** the deferred `CacheServe CLI Reference` placeholder.
- **Keep**: Available Nodes, Tool Usage Guidelines, Investigation Patterns, Domain & IP Investigation Tools, SecOps Investigation Patterns.
- **Add** a short section (3-5 sentences) explaining that available tools are advertised via MCP `tools/list` with their own per-tool documentation in the description, and that pipelines using `|` are supported between catalog tools when the chained tools are pipe-stage capable.

The expectation is that the system prompt shrinks substantially — from ~360 lines to ~150 — and the per-tool documentation is paid for in tokens only when those tools are advertised. This is the right direction for both maintainability and eventual fine-tuning.

---

## 12. Privilege Model

For v1 the MCP server runs as root (in production: as the user that owns the existing `statmon-mcp` Docker container, which is root by default per `Dockerfile`). This is documented in `docs/design.md` as a known interim posture.

Implementation implications:

- No `sudo` invocation in the executor. The binary runs directly under the server's UID.
- Tools that need elevated privileges (`tcpdump`, `lsof` of all processes, `dmidecode`, full `ss`/`netstat`, RHEL `semanage`/`semodule`) work because the server has them.
- Subprocess `env=` is sanitized regardless of UID (§6.4) — keeps behavior identical when we move to non-root.

A future spec will:

- Move the server to a dedicated `statmon-mcp` system user.
- Define a `/etc/sudoers.d/statmon-mcp` fragment whitelisting the exact catalog binaries.
- Add an executor option `use_sudo: true` (per-entry or via defaults) that prepends `sudo -n --` to invocations.

The catalog's binary-resolution-at-startup pattern (§3.3, step 6) is the bridge to this future work: every entry already caches its resolved absolute binary path, which is exactly what a sudoers fragment needs to whitelist. The implementation should expose a `statmon-mcp --dump-resolved-binaries` CLI flag that prints the resolved paths in a format suitable for piping into a sudoers-fragment generator. This makes the eventual non-root migration mechanical rather than manual.

That work is **out of scope** for this spec. Don't pre-build the `use_sudo` flag now — wait until we have the sudoers design.

---

## 13. Testing

### 13.1 New unit tests

- **`tests/test_catalog.py`**
  - Loading: valid catalog files load cleanly; entries are addressable by name.
  - Validation: missing `name`, missing both `description` and `description_file`, both present, missing `binary`, missing `timeout_seconds`, empty rules — each produces a clear error.
  - Duplicates: two entries with the same `name` across files cause startup error.
  - `description_file` resolution: relative paths resolve against the catalog dir.
  - Defaults: `timeout_seconds`, `output.max_bytes` from `catalog.defaults` apply when entry omits them.
  - Unhealthy tools: missing binary registers but is marked unhealthy; calling it returns an error envelope identifying the search paths that were tried.

- **`tests/test_binary_resolution.py`**
  - Absolute path: used as-is; missing → unhealthy.
  - Bare name: walks `search_paths` in order; first executable match wins.
  - Bare name not found in any search path: unhealthy.
  - Resolution does not consult `os.environ["PATH"]` (test by setting env to garbage).
  - Resolved path is cached on the entry; the executor receives the absolute path at call time.

- **`tests/test_pipeline.py`**
  - Grammar: forbidden metacharacters (`;`, `&`, `>`, `<`, `` ` ``, `$(`, `&&`, `||`, newline) all rejected.
  - Quoting: `|` inside quoted args is not a separator.
  - Empty segment: `ps aux | | head` rejected.
  - Pipe-stage enforcement: lead-only tool in non-lead position rejected.
  - Per-segment filter: a denied middle segment rejects the whole pipeline with the offending segment identified.

- **`tests/test_executor.py` (extends `test_cli_executor.py`)**
  - Timeout actually kills: spawn `sleep 30` with `timeout=1`; verify subprocess exit within ~1.5s.
  - Output cap: spawn a producer that emits >max_bytes; verify truncation marker and that the subprocess is killed.
  - Pipeline execution: end-to-end multi-segment pipeline with stub binaries.
  - Pipeline kill propagation: timeout in pipeline kills all segments.
  - Non-last segment non-zero exit: surfaces in `warning`, doesn't flip status.
  - Subprocess receives sanitized env: stub binary that prints its env shows only `PATH`, `LANG`, `LC_ALL`.
  - `prepend_args`: list `["statmon"]` produces `nom-tell statmon <command...>`; empty list produces `nom-tell <command...>` directly.

### 13.2 Updated tests

- **`tests/test_filter.py`**: existing tests pass unchanged. Add coverage for `check_paths`.
- **`tests/test_app.py`**, **`tests/test_anthropic_client.py`**, **`tests/test_mcp_pool.py`**: should pass unchanged — the wire protocol is preserved.

### 13.3 Integration smoke

- Bring up the server with the v1 catalog. Hit `/health`; verify `tools` list includes statmon and the v1 Linux tools available on the test host. For each tool, call with a known-safe invocation (`ps aux`, `df -h`, `ss -s`, etc.) and verify success. For each, call with a known-denied invocation and verify a `denied` envelope.
- Run smoke tests on both an Ubuntu and a RHEL-derived host (Rocky, Alma, or Fedora) to verify cross-distro behavior. Specifically: on Ubuntu, `dnf` and SELinux entries register unhealthy and return clear errors; on RHEL, `apt` and `snap` register unhealthy and return clear errors.

### 13.4 Acceptance criteria

- All existing tests pass.
- New tests pass.
- `statmon` end-to-end behavior is unchanged: same envelope, same description content (now sourced from `descriptions/statmon.md`), same denials.
- `tools/list` from a vanilla install returns ≥40 entries (statmon + the v1 Linux sweep). Specific entry count varies by host (which optional packages are installed and which family) but the catalog ships the same set everywhere.
- `find / -delete`, `systemctl restart nginx`, `journalctl --rotate`, `tcpdump -w out.pcap`, `dd if=/dev/zero of=/tmp/x` — all return `denied` envelopes.

---

## 14. Rollout Plan

Implementation order (each stage is independently mergeable and testable):

1. **Catalog scaffolding.** Add `ToolEntry`, `ToolRegistry`, loader, validator, binary resolver, tests. Server still uses hardcoded `statmon` tool; catalog is unused.
2. **Server uses catalog.** Migrate `statmon` into `catalog/statmon.yaml`. Server now reads from catalog. Hardcoded literal is removed. End-to-end tests for `statmon` must pass.
3. **Executor hardening.** Kill-on-timeout, streaming output cap, sanitized subprocess env, before any new tool is added. All existing tests pass with new executor.
4. **Pipeline grammar.** Parser, per-segment filter, pipeline executor. New tests pass; statmon (no pipes) still passes.
5. **Linux v1 catalog.** Drop in the YAML files for §9.1 through §9.13 in roughly that order. Add per-tool integration smoke tests as you go. Validate on both Ubuntu and RHEL hosts.
6. **System prompt trim.** Shrink `prompt.txt` per §11.
7. **Docs update.** Update `docs/design.md` to reflect the catalog model. Update `README.md` setup instructions.

Each stage produces a working server. If we run out of time, stopping after stage 4 still leaves us with a functioning, hardened, catalog-driven `statmon` server that's ready to grow.

---

## 15. Open Items / Deferred

- Non-root + sudoers privilege model (next spec).
- CacheServe catalog entry (waiting on real CLI reference).
- Dynamic catalog reload (SIGHUP or filesystem watch). Not needed v1; restart suffices.
- Per-tool authentication / RBAC (different operators get different tool subsets). Not needed v1.
- Per-entry `env:` override hook (e.g., for tools needing `LD_LIBRARY_PATH`). Add when first real need surfaces.
- Stronger awk/sed sandboxing via AST inspection. Deferred unless a real misuse is observed.
- Catalog validation as a standalone CLI (`statmon-mcp --validate /etc/statmon-mcp/catalog/`) for ops use. Nice-to-have.
- `nmap`, `strace`, `perf`, `iptables` (read), `ssh`, `curl`, `wget` — all deferred per §9.14; revisit when there's a clear use case.
- Firewalld/nftables read-only inspection (`firewall-cmd --list-all`, `nft list ruleset`) — RHEL-relevant; needs care because these often require elevated privileges and the read/write surface mixes in awkward ways. Defer to the sudoers spec where it can be addressed properly.

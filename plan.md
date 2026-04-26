# Implementation Plan: Catalog-Driven MCP Tool Expansion

Source spec: `docs/SPEC-catalog-driven-mcp.md`. This plan maps the spec onto concrete code changes in the repo and sequences them into independently-mergeable stages, mirroring §14 of the spec.

---

## Current state (baseline)

- `statmon-mcp/statmon_mcp/server.py` — hardcodes a single `statmon` Tool in `list_tools()`; `call_tool()` rejects anything else.
- `statmon-mcp/statmon_mcp/cli_executor.py` — single `run_cli(binary, subsystem, command, timeout)`; uses `proc.communicate()` (no kill on timeout), no output cap, no env sanitation.
- `statmon-mcp/statmon_mcp/filter.py` — `check_command()` already deny→allow→default-deny. Reusable as-is.
- Config today carries a top-level `statmon:` block (binary/subsystem/timeout/rules). To be replaced.
- Tests in `tests/`: `test_filter.py`, `test_cli_executor.py`, `test_app.py`, `test_anthropic_client.py`, `test_mcp_pool.py`, `test_cli.py`, `test_security_tools.py`, `test_trace.py`.

---

## Stage 1 — Catalog scaffolding (no behavior change)

Goal: introduce the catalog data model, loader, validator, and binary resolver. Server still uses the hardcoded `statmon` tool — catalog code is exercised only by unit tests.

**New files**

- `statmon-mcp/statmon_mcp/catalog.py`
  - `@dataclass ToolEntry`: `name`, `description` (resolved), `binary` (absolute resolved path or `None` if unhealthy), `binary_raw` (original yaml value), `prepend_args: list[str]`, `timeout_seconds: int`, `max_bytes: int`, `pipe_stage: bool`, `rules: dict`, `path_deny: list[str]`, `unhealthy_reason: str | None`, `search_paths_tried: list[str]`.
  - `class ToolRegistry`: `tools: dict[str, ToolEntry]`, plus `get(name)` and `__iter__`.
  - `load_catalog(catalog_dir, defaults, search_paths) -> ToolRegistry`: reads `*.yaml` lex-sorted, parses each as a list, calls `_validate_entry`, calls `_resolve_binary`, detects duplicate names (hard error), inlines `description_file` (relative to catalog dir).
  - `_validate_entry(entry, file_path, idx)`: enforces required fields (`name`, `binary`, exactly one of `description`/`description_file`, non-empty rules per §3.2), raises `CatalogError` with file+index context.
  - `_resolve_binary(binary, search_paths) -> tuple[str | None, list[str]]`: absolute → `os.access(p, X_OK)`; bare → walk `search_paths`. Returns `(resolved_path_or_None, paths_tried)`. Never reads `os.environ["PATH"]`.
  - `class CatalogError(Exception)`.

**New tests**

- `tests/test_catalog.py` — covers §13.1 list for catalog: valid load, missing fields, dup names, `description_file` resolution, defaults inheritance, unhealthy-on-missing-binary.
- `tests/test_binary_resolution.py` — absolute hit/miss, bare-name walks search_paths in order, ignores `PATH`, caches resolved abs path.

Stage exit: `pytest` green; server behavior unchanged.

---

## Stage 2 — Server reads from catalog; migrate `statmon`

Goal: remove the hardcoded literal; serve tools dynamically.

**Edits to `statmon-mcp/statmon_mcp/server.py`**

- At startup (lazy via `get_registry()` like `get_config()` today), call `load_catalog(...)` using `config["catalog"]` block.
- Replace `list_tools()` body with a loop over the registry building one `Tool(...)` per entry, with the unified `inputSchema` from §4.1.
- Rewrite `call_tool()` per §4.2: registry lookup → unhealthy check → filter check → dispatch to executor → wrap envelope. (Pipeline support arrives in stage 4 — for now, keep single-segment path only and reject any unquoted `|` with a "pipelines not yet supported" error if you want to defer; or just pass through to executor and let stage 4 wire the parser. Choose: pass-through with a parser stub that returns one segment.)
- `health()` returns the dynamic tool list from the registry.

**New files**

- `configs/catalog/statmon.yaml` — entry per §8.2.
- `configs/catalog/descriptions/statmon.md` — content extracted verbatim from the existing statmon CLI reference in `statmon-chat/statmon_chat/prompt.txt` (§8.3 list).
- `configs/mcp-server.example.yaml` — update to new shape (§10.1): remove `statmon:` block, add `catalog:` block.

**Updated tests**

- `tests/test_app.py`, `tests/test_mcp_pool.py`, `tests/test_anthropic_client.py` — should pass unchanged. Fixtures may need to point at a test catalog dir instead of injecting a `statmon:` block.

Stage exit: end-to-end statmon flow identical on the wire; envelope shape unchanged.

---

## Stage 3 — Executor hardening

Goal: make `cli_executor.py` safe for the broader tool set before adding any new tools.

**Edits to `statmon-mcp/statmon_mcp/cli_executor.py`**

- Refactor signature: `run_tool(entry: ToolEntry, args: str) -> dict` (single-segment path). Keep an internal `_spawn(entry, args, stdin)` that returns the live `Process`.
- Kill on timeout (§6.1): wrap in try/except; on `TimeoutError` call `proc.kill()` then `await proc.wait()`. Same in any failure path.
- Streaming output cap (§6.2): replace `communicate()` with concurrent `_read_capped(stream, max_bytes)` tasks for stdout (`entry.max_bytes`) and stderr (8 KB). On cap reached, kill subprocess and append truncation marker.
- Sanitized env (§6.4): build `SAFE_ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}` and pass `env=SAFE_ENV` to every `create_subprocess_exec`.
- Use `entry.binary` (already resolved abs path) and `entry.prepend_args + shlex.split(args)`.
- Result shape: keep current envelope. JSON-decode `result` only when single-segment AND output looks JSON-ish (cheap check or just attempt as today).

**Updated tests**

- `tests/test_cli_executor.py` extended per §13.1 executor list: real timeout kills `sleep 30`, output cap with a producer script, env sanitation via stub script that prints env, `prepend_args` splicing.

Stage exit: all existing tests + new executor tests green; statmon e2e still works.

---

## Stage 4 — Pipeline grammar & per-segment dispatch

Goal: support `lead args | stage args | stage args` per §5.

**New files**

- `statmon-mcp/statmon_mcp/pipeline.py`
  - `parse_pipeline(command: str) -> list[str]` — single-pass scanner respecting `'…'` and `"…"`; rejects unquoted `;`, `&`, `>`, `<`, `` ` ``, `$(`, `&&`, `||`, newline; rejects empty segments. Raises `PipelineGrammarError`.
  - `resolve_pipeline(lead_entry, segments, registry) -> list[tuple[ToolEntry, str]]` — first segment uses `lead_entry`; subsequent segments parse the leading token as a tool name, look it up, and require `pipe_stage=True`.

**Edits**

- `cli_executor.py`: add `run_pipeline(stages: list[tuple[ToolEntry, str]]) -> dict` per §6.3. Chain stdouts; lead's `timeout_seconds` bounds the whole pipeline; last stage's `max_bytes` caps captured stdout; on timeout/cap, kill all. Surface non-zero non-last exits in `warning`. Final exit_code = last segment's. Apply sanitized env to every stage.
- `filter.py`: add `check_paths(args: str, deny: list[str]) -> tuple[bool, str]` per §7.
- `server.py` `call_tool()`: parse → resolve → per-segment `check_command` + `check_paths` → `run_pipeline`. Add `pipeline` array in envelope only when len > 1 (preserve single-segment shape for statmon).

**New tests**

- `tests/test_pipeline.py` — grammar rejections, quoted-`|` handling, empty segments, lead-only-in-non-lead-position, denied middle segment surfaces correct envelope.
- Extend `tests/test_cli_executor.py`: multi-segment success, kill propagation on timeout, non-last non-zero → `warning` not `error`.

Stage exit: pipelines work; statmon (no pipes) unchanged.

---

## Stage 5 — Linux v1 catalog

Goal: ship the §9.1–§9.13 catalog files.

**New files** (under `configs/catalog/`)

`linux-process.yaml`, `linux-fs.yaml`, `linux-disk.yaml`, `linux-net.yaml`, `linux-system.yaml`, `linux-logs.yaml`, `linux-systemd.yaml`, `linux-packages-deb.yaml`, `linux-packages-rpm.yaml`, `linux-text.yaml`, `linux-containers.yaml`, `linux-kernel.yaml`, `linux-selinux.yaml`.

Each entry uses bare `binary:` names; entries follow the verb-allow vs flag-based idioms from §9.0. `linux-text.yaml` entries set `pipe_stage: true` and include the `awk`/`sed`/`tail` deny patterns from §9.10. Every spec table maps 1:1 to entries.

**Smoke test**

- `tests/test_catalog_v1.py` (lightweight): for each shipped YAML file, assert it loads, all entries validate, and rules are non-empty. Don't run the binaries — that's local dev / integration smoke per §13.3.

Stage exit: `tools/list` returns ≥40 entries on a typical host; denied invocations from §13.4 produce `denied` envelopes.

---

## Stage 6 — System prompt trim

Goal: §11 — drop the inlined statmon CLI reference (now in `descriptions/statmon.md`); keep nodes / usage / investigation patterns / SecOps; add a short blurb about MCP-advertised tool descriptions and `|` pipelines.

**Edits**

- `statmon-chat/statmon_chat/prompt.txt` — delete Statmon Querystore CLI Reference, Authoritative Querystore section, deferred CacheServe placeholder. Add the 3–5-sentence MCP/pipelines section.
- Verify `system_prompt.py`'s `{nodes_section}` injection still works.

Stage exit: prompt shrinks substantially (~360 → ~150 lines target per spec); existing chat tests pass.

---

## Stage 7 — Docs & migration helper

- `docs/design.md` — describe the catalog model, binary resolution, pipeline grammar, sanitized env.
- `README.md` — update setup steps to reference `configs/catalog/`.
- `configs/migrate-to-catalog.sh` — read an old `config.yaml` with a `statmon:` block, emit a new `config.yaml` with `catalog:` block plus `catalog/statmon.yaml` and `catalog/descriptions/statmon.md` alongside (§10.2).

---

## Out of scope (per spec §15)

Non-root + sudoers, CacheServe entry, dynamic reload, RBAC, per-entry env override, awk/sed AST sandbox, `--validate` / `--dump-resolved-binaries` CLI, `nmap`/`strace`/`perf`/firewall tools.

---

## Risks / things to watch

- **Test fixtures** — several existing tests likely build a `config` dict with a `statmon:` block. Stage 2 needs a small fixture helper that writes a temp catalog dir so we don't pepper tests with inline YAML.
- **`description_file` on a fresh checkout** — the loader must resolve relative to the catalog dir, not CWD. Cover in `test_catalog.py`.
- **Pipeline timeout accounting** — applying the lead's timeout to the whole pipeline is what the spec mandates; document it in `journalctl`/`tcpdump` descriptions so operators size them sensibly.
- **`prepend_args` order** — spec is explicit: `binary` then `prepend_args` then user args. Stage 3 test must lock this in (covers the `nom-tell statmon …` shape).
- **Backward-compat config** — spec offers either a migration script or a startup shim; this plan picks the script (§10.2 preferred path). If we want zero-touch upgrades, add the shim later.

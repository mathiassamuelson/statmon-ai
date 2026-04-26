"""Catalog-driven tool registry for statmon-mcp.

Loads YAML tool entries from a catalog directory, validates them, resolves
binary paths against a configured search_paths list (never PATH), and exposes
them as a ToolRegistry the server uses to advertise and dispatch MCP tools.

See docs/SPEC-catalog-driven-mcp.md §3 for the full schema and semantics.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import yaml


class CatalogError(Exception):
    """Raised for any catalog parse, validation, or duplicate-name error."""


DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_BYTES = 65536


@dataclass
class ToolEntry:
    name: str
    description: str
    binary_raw: str
    binary: str | None
    prepend_args: list[str]
    timeout_seconds: int
    max_bytes: int
    pipe_stage: bool
    rules: dict
    path_deny: list[str] = field(default_factory=list)
    unhealthy_reason: str | None = None
    search_paths_tried: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return self.unhealthy_reason is None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}

    def add(self, entry: ToolEntry) -> None:
        if entry.name in self._tools:
            raise CatalogError(f"Duplicate tool name: {entry.name!r}")
        self._tools[entry.name] = entry

    def get(self, name: str) -> ToolEntry | None:
        return self._tools.get(name)

    def __iter__(self) -> Iterator[ToolEntry]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools.keys())


def _validate_entry(entry: dict, file_path: Path, idx: int) -> None:
    where = f"{file_path}#[{idx}]"
    if not isinstance(entry, dict):
        raise CatalogError(f"{where}: entry must be a mapping, got {type(entry).__name__}")

    name = entry.get("name")
    if not name or not isinstance(name, str):
        raise CatalogError(f"{where}: missing or invalid 'name'")

    has_desc = "description" in entry
    has_desc_file = "description_file" in entry
    if has_desc == has_desc_file:
        raise CatalogError(
            f"{where} ({name}): exactly one of 'description' or 'description_file' is required"
        )

    binary = entry.get("binary")
    if not binary or not isinstance(binary, str):
        raise CatalogError(f"{where} ({name}): missing or invalid 'binary'")

    rules = entry.get("rules")
    if not isinstance(rules, dict):
        raise CatalogError(f"{where} ({name}): 'rules' must be a mapping")
    deny = rules.get("deny", []) or []
    allow = rules.get("allow", []) or []
    if not deny and not allow:
        raise CatalogError(
            f"{where} ({name}): rules must define at least one of 'deny' or 'allow' "
            "(empty rules would default-deny everything)"
        )

    prepend = entry.get("prepend_args", [])
    if not isinstance(prepend, list) or not all(isinstance(x, str) for x in prepend):
        raise CatalogError(f"{where} ({name}): 'prepend_args' must be a list of strings")

    output = entry.get("output", {})
    if output and not isinstance(output, dict):
        raise CatalogError(f"{where} ({name}): 'output' must be a mapping")

    path_rules = entry.get("path_rules", {})
    if path_rules and not isinstance(path_rules, dict):
        raise CatalogError(f"{where} ({name}): 'path_rules' must be a mapping")


def _resolve_binary(binary: str, search_paths: list[str]) -> tuple[str | None, list[str]]:
    """Resolve a binary reference to an absolute executable path.

    Absolute paths are checked directly. Bare names walk search_paths in order.
    Never consults os.environ["PATH"]. Returns (resolved_or_None, paths_tried).
    """
    if os.path.isabs(binary):
        if os.path.isfile(binary) and os.access(binary, os.X_OK):
            return binary, [binary]
        return None, [binary]

    tried: list[str] = []
    for d in search_paths:
        candidate = os.path.join(d, binary)
        tried.append(candidate)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate, tried
    return None, tried


def _load_description(entry: dict, catalog_dir: Path) -> str:
    if "description" in entry:
        return str(entry["description"])
    rel = entry["description_file"]
    desc_path = (catalog_dir / rel).resolve()
    try:
        return desc_path.read_text()
    except OSError as e:
        raise CatalogError(
            f"entry {entry.get('name')!r}: description_file {rel!r} not readable ({e})"
        )


def _build_entry(raw: dict, catalog_dir: Path, defaults: dict, search_paths: list[str]) -> ToolEntry:
    output = {**defaults.get("output", {}), **(raw.get("output") or {})}
    timeout = raw.get("timeout_seconds", defaults.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    max_bytes = output.get("max_bytes", DEFAULT_MAX_BYTES)
    binary_raw = raw["binary"]
    resolved, tried = _resolve_binary(binary_raw, search_paths)
    unhealthy = None
    if resolved is None:
        unhealthy = (
            f"binary {binary_raw!r} not found or not executable "
            f"(tried: {', '.join(tried) if tried else '<none>'})"
        )

    rules = raw.get("rules") or {}
    path_rules = raw.get("path_rules") or {}

    return ToolEntry(
        name=raw["name"],
        description=_load_description(raw, catalog_dir),
        binary_raw=binary_raw,
        binary=resolved,
        prepend_args=list(raw.get("prepend_args") or []),
        timeout_seconds=int(timeout),
        max_bytes=int(max_bytes),
        pipe_stage=bool(raw.get("pipe_stage", False)),
        rules={"deny": list(rules.get("deny") or []), "allow": list(rules.get("allow") or [])},
        path_deny=list(path_rules.get("deny") or []),
        unhealthy_reason=unhealthy,
        search_paths_tried=tried,
    )


def load_catalog(
    catalog_dir: str | os.PathLike,
    defaults: dict | None = None,
    search_paths: list[str] | None = None,
) -> ToolRegistry:
    """Load all *.yaml tool entries under catalog_dir into a ToolRegistry.

    Files are read in lexicographic order. Subdirectories are ignored.
    Validation errors and duplicate names raise CatalogError; missing binaries
    register as unhealthy entries (no exception).
    """
    catalog_path = Path(catalog_dir)
    if not catalog_path.is_dir():
        raise CatalogError(f"catalog directory does not exist: {catalog_path}")

    defaults = defaults or {}
    search_paths = search_paths or []

    registry = ToolRegistry()
    for yaml_path in sorted(catalog_path.glob("*.yaml")):
        try:
            with open(yaml_path) as f:
                doc = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise CatalogError(f"{yaml_path}: invalid YAML ({e})")

        if doc is None:
            continue
        if not isinstance(doc, list):
            raise CatalogError(
                f"{yaml_path}: top-level must be a list of tool entries, got {type(doc).__name__}"
            )

        for idx, raw in enumerate(doc):
            _validate_entry(raw, yaml_path, idx)
            entry = _build_entry(raw, catalog_path, defaults, search_paths)
            registry.add(entry)

    return registry

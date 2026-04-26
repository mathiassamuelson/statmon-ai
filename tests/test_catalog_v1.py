"""Smoke tests for the shipped Linux v1 catalog.

These don't run any binaries — they verify each YAML file parses, every
entry validates, and the registry comes out non-empty. Real per-tool
behavior is integration smoke (spec §13.3).
"""

import os
from pathlib import Path

import pytest

from statmon_mcp.catalog import load_catalog

CATALOG_DIR = Path(__file__).parent.parent / "configs" / "catalog"


def _all_search_paths():
    # A maximal search path so tools that exist on the test host resolve.
    # Tools missing locally just register as unhealthy.
    return [
        "/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin",
        "/sbin", "/bin", "/opt/homebrew/bin", "/opt/homebrew/sbin",
    ]


def test_catalog_dir_exists():
    assert CATALOG_DIR.is_dir(), f"catalog dir missing: {CATALOG_DIR}"


def test_catalog_loads_clean():
    reg = load_catalog(
        CATALOG_DIR,
        defaults={"timeout_seconds": 30, "output": {"max_bytes": 65536}},
        search_paths=_all_search_paths(),
    )
    # Should easily exceed 40 entries (statmon + the v1 sweep).
    assert len(reg) >= 40, f"only {len(reg)} entries loaded"


def test_expected_entries_present():
    reg = load_catalog(
        CATALOG_DIR,
        defaults={"timeout_seconds": 30, "output": {"max_bytes": 65536}},
        search_paths=_all_search_paths(),
    )
    expected = {
        # statmon migration
        "statmon",
        # process
        "ps", "pgrep", "top",
        # fs
        "ls", "df", "find", "mount",
        # net
        "ip", "ss", "ping", "tcpdump", "dig",
        # logs
        "journalctl", "dmesg",
        # systemd
        "systemctl",
        # packages — both families ship
        "apt", "dpkg", "dnf", "rpm",
        # text / pipe stages
        "grep", "head", "tail", "awk", "sed", "wc", "sort",
        # containers
        "docker", "kubectl",
        # selinux
        "getenforce", "semodule",
        # kernel
        "lsmod",
    }
    missing = expected - set(reg.names())
    assert not missing, f"missing: {missing}"


def test_text_processors_are_pipe_stages():
    reg = load_catalog(
        CATALOG_DIR,
        defaults={"timeout_seconds": 30, "output": {"max_bytes": 65536}},
        search_paths=_all_search_paths(),
    )
    for name in ("grep", "head", "tail", "awk", "sed", "wc", "sort", "cut", "tr", "cat"):
        e = reg.get(name)
        assert e is not None, name
        assert e.pipe_stage, f"{name} should be a pipe stage"


def test_lead_only_tools_are_not_pipe_stages():
    reg = load_catalog(
        CATALOG_DIR,
        defaults={"timeout_seconds": 30, "output": {"max_bytes": 65536}},
        search_paths=_all_search_paths(),
    )
    for name in ("ps", "ls", "df", "ip", "ss", "tcpdump", "systemctl", "docker", "kubectl"):
        e = reg.get(name)
        assert e is not None, name
        assert not e.pipe_stage, f"{name} should be lead-only"


def test_dangerous_invocations_blocked():
    """Spot-check that the spec's §13.4 acceptance examples deny correctly."""
    from statmon_mcp.filter import check_command

    reg = load_catalog(
        CATALOG_DIR,
        defaults={"timeout_seconds": 30, "output": {"max_bytes": 65536}},
        search_paths=_all_search_paths(),
    )

    cases = [
        ("find", "/ -delete"),
        ("journalctl", "--rotate"),
        ("tcpdump", "-w out.pcap"),
        ("systemctl", "restart nginx"),
        ("dpkg", "-i some.deb"),
        ("dnf", "install nginx"),
    ]
    for tool, cmd in cases:
        e = reg.get(tool)
        assert e is not None, tool
        ok, _ = check_command(cmd, e.rules)
        assert not ok, f"{tool} {cmd!r} should be denied"

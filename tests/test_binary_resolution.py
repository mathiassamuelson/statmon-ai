"""Tests for statmon_mcp.catalog._resolve_binary."""

import os
import pytest

from statmon_mcp.catalog import _resolve_binary


@pytest.fixture
def bins(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "tool1").write_text("#!/bin/sh\n")
    (a / "tool1").chmod(0o755)
    (b / "tool1").write_text("#!/bin/sh\n")
    (b / "tool1").chmod(0o755)
    (b / "tool2").write_text("#!/bin/sh\n")
    (b / "tool2").chmod(0o755)
    return str(a), str(b)


def test_absolute_hit(tmp_path):
    p = tmp_path / "x"
    p.write_text("#!/bin/sh\n")
    p.chmod(0o755)
    resolved, tried = _resolve_binary(str(p), [])
    assert resolved == str(p)
    assert tried == [str(p)]


def test_absolute_missing(tmp_path):
    p = tmp_path / "missing"
    resolved, tried = _resolve_binary(str(p), ["/ignored"])
    assert resolved is None
    assert tried == [str(p)]


def test_absolute_not_executable(tmp_path):
    p = tmp_path / "x"
    p.write_text("not exec")
    p.chmod(0o644)
    resolved, _ = _resolve_binary(str(p), [])
    assert resolved is None


def test_bare_name_first_match_wins(bins):
    a, b = bins
    resolved, tried = _resolve_binary("tool1", [a, b])
    assert resolved == os.path.join(a, "tool1")
    # Stops at first match; should not have visited b.
    assert tried == [os.path.join(a, "tool1")]


def test_bare_name_falls_through(bins):
    a, b = bins
    resolved, tried = _resolve_binary("tool2", [a, b])
    assert resolved == os.path.join(b, "tool2")
    assert tried == [os.path.join(a, "tool2"), os.path.join(b, "tool2")]


def test_bare_name_not_found(bins):
    a, b = bins
    resolved, tried = _resolve_binary("nope", [a, b])
    assert resolved is None
    assert tried == [os.path.join(a, "nope"), os.path.join(b, "nope")]


def test_does_not_consult_path(bins, monkeypatch):
    a, _ = bins
    monkeypatch.setenv("PATH", "/garbage:/also-garbage")
    resolved, _ = _resolve_binary("tool1", [a])
    assert resolved == os.path.join(a, "tool1")
    # And with no search paths, even an existing system tool is unfound.
    resolved2, _ = _resolve_binary("ls", [])
    assert resolved2 is None

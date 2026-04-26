"""Tests for statmon_mcp.catalog — YAML loader, validator, registry."""

import textwrap
import pytest

from statmon_mcp.catalog import (
    CatalogError,
    ToolRegistry,
    load_catalog,
)


def write(path, content):
    path.write_text(textwrap.dedent(content))


@pytest.fixture
def catalog_dir(tmp_path):
    d = tmp_path / "catalog"
    d.mkdir()
    return d


@pytest.fixture
def search_paths(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "fake-tool"
    fake.write_text("#!/bin/sh\necho ok\n")
    fake.chmod(0o755)
    return [str(bin_dir)]


def test_load_valid_catalog(catalog_dir, search_paths):
    write(catalog_dir / "a.yaml", """
        - name: fake-tool
          description: A fake tool.
          binary: fake-tool
          rules:
            allow: ["*"]
    """)
    reg = load_catalog(catalog_dir, defaults={}, search_paths=search_paths)
    assert isinstance(reg, ToolRegistry)
    assert "fake-tool" in reg
    entry = reg.get("fake-tool")
    assert entry.healthy
    assert entry.binary.endswith("/fake-tool")
    assert entry.timeout_seconds == 30
    assert entry.max_bytes == 65536


def test_missing_name_raises(catalog_dir, search_paths):
    write(catalog_dir / "a.yaml", """
        - description: hi
          binary: fake-tool
          rules:
            allow: ["*"]
    """)
    with pytest.raises(CatalogError, match="name"):
        load_catalog(catalog_dir, search_paths=search_paths)


def test_both_description_and_file_raises(catalog_dir, search_paths):
    write(catalog_dir / "a.yaml", """
        - name: x
          description: inline
          description_file: foo.md
          binary: fake-tool
          rules:
            allow: ["*"]
    """)
    with pytest.raises(CatalogError, match="description"):
        load_catalog(catalog_dir, search_paths=search_paths)


def test_missing_description_raises(catalog_dir, search_paths):
    write(catalog_dir / "a.yaml", """
        - name: x
          binary: fake-tool
          rules:
            allow: ["*"]
    """)
    with pytest.raises(CatalogError, match="description"):
        load_catalog(catalog_dir, search_paths=search_paths)


def test_missing_binary_raises(catalog_dir, search_paths):
    write(catalog_dir / "a.yaml", """
        - name: x
          description: y
          rules:
            allow: ["*"]
    """)
    with pytest.raises(CatalogError, match="binary"):
        load_catalog(catalog_dir, search_paths=search_paths)


def test_empty_rules_raises(catalog_dir, search_paths):
    write(catalog_dir / "a.yaml", """
        - name: x
          description: y
          binary: fake-tool
          rules: {}
    """)
    with pytest.raises(CatalogError, match="rules"):
        load_catalog(catalog_dir, search_paths=search_paths)


def test_duplicate_name_across_files_raises(catalog_dir, search_paths):
    entry = """
        - name: dup
          description: y
          binary: fake-tool
          rules:
            allow: ["*"]
    """
    write(catalog_dir / "a.yaml", entry)
    write(catalog_dir / "b.yaml", entry)
    with pytest.raises(CatalogError, match="Duplicate"):
        load_catalog(catalog_dir, search_paths=search_paths)


def test_description_file_resolution(catalog_dir, search_paths):
    desc_dir = catalog_dir / "descriptions"
    desc_dir.mkdir()
    (desc_dir / "x.md").write_text("Long-form docs for x.")
    write(catalog_dir / "a.yaml", """
        - name: x
          description_file: descriptions/x.md
          binary: fake-tool
          rules:
            allow: ["*"]
    """)
    reg = load_catalog(catalog_dir, search_paths=search_paths)
    assert reg.get("x").description == "Long-form docs for x."


def test_defaults_inheritance(catalog_dir, search_paths):
    write(catalog_dir / "a.yaml", """
        - name: x
          description: y
          binary: fake-tool
          rules:
            allow: ["*"]
        - name: y
          description: z
          binary: fake-tool
          timeout_seconds: 99
          output:
            max_bytes: 1024
          rules:
            allow: ["*"]
    """)
    reg = load_catalog(
        catalog_dir,
        defaults={"timeout_seconds": 45, "output": {"max_bytes": 8192}},
        search_paths=search_paths,
    )
    x = reg.get("x")
    y = reg.get("y")
    assert x.timeout_seconds == 45
    assert x.max_bytes == 8192
    assert y.timeout_seconds == 99
    assert y.max_bytes == 1024


def test_unhealthy_when_binary_missing(catalog_dir, search_paths):
    write(catalog_dir / "a.yaml", """
        - name: nope
          description: y
          binary: does-not-exist
          rules:
            allow: ["*"]
    """)
    reg = load_catalog(catalog_dir, search_paths=search_paths)
    entry = reg.get("nope")
    assert not entry.healthy
    assert "does-not-exist" in entry.unhealthy_reason
    assert entry.search_paths_tried


def test_pipe_stage_and_path_rules(catalog_dir, search_paths):
    write(catalog_dir / "a.yaml", """
        - name: cat
          description: y
          binary: fake-tool
          pipe_stage: true
          rules:
            allow: ["*"]
          path_rules:
            deny:
              - "/etc/shadow"
    """)
    reg = load_catalog(catalog_dir, search_paths=search_paths)
    e = reg.get("cat")
    assert e.pipe_stage is True
    assert e.path_deny == ["/etc/shadow"]


def test_top_level_must_be_list(catalog_dir, search_paths):
    write(catalog_dir / "a.yaml", """
        name: x
        description: y
    """)
    with pytest.raises(CatalogError, match="list"):
        load_catalog(catalog_dir, search_paths=search_paths)


def test_lexicographic_load_order(catalog_dir, search_paths):
    # Duplicate name detection across files implicitly relies on iterating
    # all yaml files. Verify both files are visited regardless of order.
    write(catalog_dir / "z.yaml", """
        - name: a
          description: from z
          binary: fake-tool
          rules:
            allow: ["*"]
    """)
    write(catalog_dir / "a.yaml", """
        - name: b
          description: from a
          binary: fake-tool
          rules:
            allow: ["*"]
    """)
    reg = load_catalog(catalog_dir, search_paths=search_paths)
    assert set(reg.names()) == {"a", "b"}

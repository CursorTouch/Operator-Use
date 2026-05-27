"""Tests for the Knowledge service: file discovery and system-prompt injection."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from operator_use.knowledge.service import Knowledge


@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# ── list_files ────────────────────────────────────────────────────────────────

class TestListFiles:
    def test_empty_dir_returns_empty(self, tmpdir):
        k = Knowledge(tmpdir)
        assert k.list_files() == []

    def test_nonexistent_dir_returns_empty(self, tmpdir):
        k = Knowledge(tmpdir / "missing")
        assert k.list_files() == []

    def test_flat_md_file_discovered(self, tmpdir):
        (tmpdir / "company.md").write_text("# ACME Corp\nWe build things.")
        k = Knowledge(tmpdir)
        files = k.list_files()
        assert len(files) == 1
        assert files[0]['name'] == 'company'

    def test_index_md_creates_directory_node(self, tmpdir):
        sub = tmpdir / "products"
        sub.mkdir()
        (sub / "index.md").write_text("# Products overview")
        k = Knowledge(tmpdir)
        files = k.list_files()
        assert len(files) == 1
        assert files[0]['name'] == 'products'

    def test_nested_index_md(self, tmpdir):
        sub = tmpdir / "api" / "v2"
        sub.mkdir(parents=True)
        (sub / "index.md").write_text("# API v2")
        k = Knowledge(tmpdir)
        files = k.list_files()
        assert any(f['name'] == 'api/v2' for f in files)

    def test_index_md_not_listed_as_flat_file(self, tmpdir):
        sub = tmpdir / "docs"
        sub.mkdir()
        (sub / "index.md").write_text("# Docs")
        k = Knowledge(tmpdir)
        names = [f['name'] for f in k.list_files()]
        assert 'docs/index' not in names
        assert 'docs' in names

    def test_multiple_flat_files_sorted(self, tmpdir):
        (tmpdir / "zebra.md").write_text("Z")
        (tmpdir / "alpha.md").write_text("A")
        k = Knowledge(tmpdir)
        names = [f['name'] for f in k.list_files()]
        assert names == sorted(names)

    def test_preview_returns_first_non_empty_line(self, tmpdir):
        (tmpdir / "info.md").write_text("\n# Title\nSome description here.")
        k = Knowledge(tmpdir)
        files = k.list_files()
        assert files[0]['preview'] == 'Title'

    def test_preview_truncated_to_120_chars(self, tmpdir):
        long_text = "x" * 200
        (tmpdir / "long.md").write_text(long_text)
        k = Knowledge(tmpdir)
        assert len(k.list_files()[0]['preview']) <= 120


class TestMultipleDirs:
    def test_project_dir_takes_precedence(self, tmpdir):
        global_dir = tmpdir / "global"
        project_dir = tmpdir / "project"
        global_dir.mkdir()
        project_dir.mkdir()

        (global_dir / "pricing.md").write_text("# Global pricing")
        (project_dir / "pricing.md").write_text("# Project pricing")

        k = Knowledge(project_dir, global_dir)  # project first = higher priority
        files = k.list_files()
        assert len(files) == 1
        assert files[0]['path'] == project_dir / "pricing.md"

    def test_unique_files_from_both_dirs(self, tmpdir):
        global_dir = tmpdir / "global"
        project_dir = tmpdir / "project"
        global_dir.mkdir()
        project_dir.mkdir()

        (global_dir / "shared.md").write_text("# Shared")
        (global_dir / "global_only.md").write_text("# Global only")
        (project_dir / "project_only.md").write_text("# Project only")

        k = Knowledge(project_dir, global_dir)
        names = {f['name'] for f in k.list_files()}
        assert names == {'shared', 'global_only', 'project_only'}


# ── build_knowledge_index ────────────────────────────────────────────────────

class TestBuildKnowledgeIndex:
    def test_returns_none_when_empty(self, tmpdir):
        k = Knowledge(tmpdir)
        assert k.build_knowledge_index() is None

    def test_returns_string_when_files_exist(self, tmpdir):
        (tmpdir / "pricing.md").write_text("# Pricing")
        k = Knowledge(tmpdir)
        result = k.build_knowledge_index()
        assert isinstance(result, str)

    def test_starts_with_knowledge_header(self, tmpdir):
        (tmpdir / "api.md").write_text("# API")
        k = Knowledge(tmpdir)
        result = k.build_knowledge_index()
        assert result.startswith('## Knowledge')

    def test_file_name_in_index(self, tmpdir):
        (tmpdir / "faq.md").write_text("# FAQ")
        k = Knowledge(tmpdir)
        result = k.build_knowledge_index()
        assert 'faq' in result

    def test_path_in_index(self, tmpdir):
        (tmpdir / "guide.md").write_text("# Guide")
        k = Knowledge(tmpdir)
        result = k.build_knowledge_index()
        assert str(tmpdir) in result

    def test_grouped_by_top_level_dir(self, tmpdir):
        sub = tmpdir / "products"
        sub.mkdir()
        (sub / "index.md").write_text("# Products")
        (sub / "widget.md").write_text("# Widget")
        k = Knowledge(tmpdir)
        result = k.build_knowledge_index()
        assert '**products/**' in result

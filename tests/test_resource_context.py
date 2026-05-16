"""Tests for resource/context.py: load_project_context_files."""
import pytest
from pathlib import Path

from program.resource.context import load_project_context_files


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ── Basic loading ─────────────────────────────────────────────────────────────

class TestLoadProjectContextFiles:
    def test_no_files_returns_empty(self, tmp_path):
        cwd = tmp_path / "project"
        cwd.mkdir()
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        result = load_project_context_files(cwd, agent_dir)
        assert result == []

    def test_loads_claude_md_from_agent_dir(self, tmp_path):
        cwd = tmp_path / "project"
        cwd.mkdir()
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        _write(agent_dir / "CLAUDE.md", "global instructions")

        result = load_project_context_files(cwd, agent_dir)
        assert len(result) == 1
        assert "global instructions" in result[0].content

    def test_loads_agents_md_from_agent_dir(self, tmp_path):
        cwd = tmp_path / "project"
        cwd.mkdir()
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        _write(agent_dir / "AGENTS.md", "agents content")

        result = load_project_context_files(cwd, agent_dir)
        assert len(result) == 1
        assert "agents content" in result[0].content

    def test_loads_claude_md_from_cwd(self, tmp_path):
        cwd = tmp_path / "project"
        cwd.mkdir()
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        _write(cwd / "CLAUDE.md", "project instructions")

        result = load_project_context_files(cwd, agent_dir)
        assert any("project instructions" in f.content for f in result)

    def test_global_before_local(self, tmp_path):
        cwd = tmp_path / "project"
        cwd.mkdir()
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        _write(agent_dir / "CLAUDE.md", "global")
        _write(cwd / "CLAUDE.md", "local")

        result = load_project_context_files(cwd, agent_dir)
        assert len(result) == 2
        assert "global" in result[0].content
        assert "local" in result[1].content

    def test_ancestor_directories_loaded(self, tmp_path):
        # project/sub/deep
        deep = tmp_path / "project" / "sub" / "deep"
        deep.mkdir(parents=True)
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        _write(tmp_path / "project" / "CLAUDE.md", "project-root context")
        _write(tmp_path / "project" / "sub" / "CLAUDE.md", "sub context")

        result = load_project_context_files(deep, agent_dir)
        contents = [f.content for f in result]
        assert any("project-root context" in c for c in contents)
        assert any("sub context" in c for c in contents)

    def test_ancestors_ordered_root_to_cwd(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        _write(tmp_path / "a" / "CLAUDE.md", "level-a")
        _write(tmp_path / "a" / "b" / "CLAUDE.md", "level-b")
        _write(tmp_path / "a" / "b" / "c" / "CLAUDE.md", "level-c")

        result = load_project_context_files(deep, agent_dir)
        # global is first (none here), then root→cwd order
        contents = [f.content for f in result]
        idx_a = next(i for i, c in enumerate(contents) if "level-a" in c)
        idx_b = next(i for i, c in enumerate(contents) if "level-b" in c)
        idx_c = next(i for i, c in enumerate(contents) if "level-c" in c)
        assert idx_a < idx_b < idx_c

    def test_no_duplicate_files(self, tmp_path):
        cwd = tmp_path / "project"
        cwd.mkdir()
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        _write(cwd / "CLAUDE.md", "content")

        result = load_project_context_files(cwd, agent_dir)
        paths = [f.path for f in result]
        assert len(paths) == len(set(paths))

    def test_uppercase_variant_loaded(self, tmp_path):
        cwd = tmp_path / "project"
        cwd.mkdir()
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        _write(cwd / "AGENTS.MD", "uppercase agents")

        result = load_project_context_files(cwd, agent_dir)
        assert any("uppercase agents" in f.content for f in result)

    def test_claude_md_takes_priority_over_agents_md_in_same_dir(self, tmp_path):
        cwd = tmp_path / "project"
        cwd.mkdir()
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        _write(cwd / "AGENTS.md", "agents content")
        _write(cwd / "CLAUDE.md", "claude content")

        result = load_project_context_files(cwd, agent_dir)
        # Only one file per directory (first match wins per _CONTEXT_FILENAMES order)
        cwd_files = [f for f in result if str(cwd) in f.path]
        assert len(cwd_files) == 1

    def test_path_stored_in_result(self, tmp_path):
        cwd = tmp_path / "project"
        cwd.mkdir()
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        target = _write(cwd / "CLAUDE.md", "hello")

        result = load_project_context_files(cwd, agent_dir)
        assert any(str(target) == f.path for f in result)

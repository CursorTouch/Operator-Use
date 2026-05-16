"""Tests for prompts/builder.py: system prompt construction."""
import pytest
from program.prompt.builder import build_system_prompt, _build_guidelines, _build_tools_list
from program.prompt.types import SystemPromptOptions, ContextFile


def opts(**kwargs) -> SystemPromptOptions:
    defaults = dict(cwd="/project")
    defaults.update(kwargs)
    return SystemPromptOptions(**defaults)


# ── build_system_prompt: default (no custom_prompt) ──────────────────────────

class TestBuildSystemPromptDefault:
    def test_contains_cwd(self):
        out = build_system_prompt(opts(cwd="/my/project"))
        assert "/my/project" in out

    def test_contains_today_date(self):
        from datetime import date
        out = build_system_prompt(opts())
        assert date.today().isoformat() in out

    def test_default_tools_used_when_none_specified(self):
        out = build_system_prompt(opts(tool_snippets={"read": "Read a file"}))
        assert "read" in out

    def test_selected_tools_override_defaults(self):
        out = build_system_prompt(opts(
            selected_tools=["bash"],
            tool_snippets={"bash": "Run a shell command"},
        ))
        assert "bash" in out

    def test_no_read_tool_means_no_skills_section(self):
        from program.skill.types import Skill, SourceInfo
        from pathlib import Path
        skill = Skill(
            name="my-skill",
            description="Does things",
            file_path=Path("/skills/my-skill/SKILL.md"),
            base_dir=Path("/skills/my-skill"),
            source_info=SourceInfo(path="/skills/my-skill/SKILL.md", source="user"),
        )
        out = build_system_prompt(opts(
            selected_tools=["bash"],
            skills=[skill],
        ))
        assert "my-skill" not in out

    def test_read_tool_includes_skills(self):
        from program.skill.types import Skill, SourceInfo
        from pathlib import Path
        skill = Skill(
            name="my-skill",
            description="Does things",
            file_path=Path("/skills/my-skill/SKILL.md"),
            base_dir=Path("/skills/my-skill"),
            source_info=SourceInfo(path="/skills/my-skill/SKILL.md", source="user"),
        )
        out = build_system_prompt(opts(
            selected_tools=["read"],
            skills=[skill],
        ))
        assert "my-skill" in out

    def test_append_system_prompt_included(self):
        out = build_system_prompt(opts(append_system_prompt="EXTRA INSTRUCTIONS"))
        assert "EXTRA INSTRUCTIONS" in out

    def test_context_files_included(self):
        cf = ContextFile(path="RULES.md", content="Never use global state.")
        out = build_system_prompt(opts(context_files=[cf]))
        assert "Never use global state." in out
        assert "RULES.md" in out

    def test_windows_path_normalized(self):
        out = build_system_prompt(opts(cwd=r"C:\Users\joe\project"))
        assert "\\" not in out

    def test_guidelines_in_output(self):
        out = build_system_prompt(opts(
            selected_tools=["bash"],
            prompt_guidelines=["Always explain your reasoning"],
        ))
        assert "Always explain your reasoning" in out


# ── build_system_prompt: custom_prompt ───────────────────────────────────────

class TestBuildSystemPromptCustom:
    def test_uses_custom_prompt_as_base(self):
        out = build_system_prompt(opts(custom_prompt="MY CUSTOM PROMPT"))
        assert "MY CUSTOM PROMPT" in out
        assert "coding assistant" not in out

    def test_custom_prompt_still_gets_footer(self):
        from datetime import date
        out = build_system_prompt(opts(custom_prompt="CUSTOM"))
        assert date.today().isoformat() in out

    def test_custom_prompt_with_append(self):
        out = build_system_prompt(opts(
            custom_prompt="BASE",
            append_system_prompt="APPENDED",
        ))
        assert "BASE" in out
        assert "APPENDED" in out

    def test_custom_prompt_with_context_files(self):
        cf = ContextFile(path="ctx.md", content="ctx content")
        out = build_system_prompt(opts(custom_prompt="BASE", context_files=[cf]))
        assert "ctx content" in out


# ── _build_guidelines ─────────────────────────────────────────────────────────

class TestBuildGuidelines:
    def test_bash_only_generic_guidance(self):
        out = _build_guidelines(["bash"], [])
        assert "bash" in out.lower()

    def test_bash_with_grep_prefers_grep(self):
        out = _build_guidelines(["bash", "grep"], [])
        assert "grep" in out

    def test_extra_guidelines_included(self):
        out = _build_guidelines([], ["Be helpful"])
        assert "Be helpful" in out

    def test_deduplicates_guidelines(self):
        out = _build_guidelines([], ["Same thing", "Same thing"])
        assert out.count("Same thing") == 1

    def test_base_guidelines_always_present(self):
        out = _build_guidelines([], [])
        assert "concise" in out.lower()

    def test_empty_extra_guidelines_stripped(self):
        out = _build_guidelines([], ["", "  ", "real guideline"])
        assert "real guideline" in out


# ── _build_tools_list ─────────────────────────────────────────────────────────

class TestBuildToolsList:
    def test_shows_tools_with_snippets(self):
        out = _build_tools_list(["read", "bash"], {"read": "Reads files", "bash": "Runs shell"})
        assert "read: Reads files" in out
        assert "bash: Runs shell" in out

    def test_none_when_no_matching_snippets(self):
        out = _build_tools_list(["read"], {})
        assert out == "(none)"

    def test_tools_without_snippet_excluded(self):
        out = _build_tools_list(["read", "bash"], {"read": "Reads files"})
        assert "bash" not in out

    def test_empty_tools_returns_none(self):
        out = _build_tools_list([], {"read": "Reads files"})
        assert out == "(none)"

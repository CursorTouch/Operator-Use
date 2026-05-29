"""Tests for prompt/builder.py and prompt/utils.py."""
import pytest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

from operator_use.prompt.builder import PromptTemplate, build_system_prompt
from operator_use.prompt.types import SystemPromptOptions
from operator_use.prompt.utils import build_guidelines, format_skills_for_prompt
from operator_use.skill.types import Skill, SourceInfo


def make_tool(name: str):
    tool = MagicMock()
    tool.name = name
    return tool


def make_skill(name: str, description: str = "Does things", disabled: bool = False) -> Skill:
    return Skill(
        name=name,
        description=description,
        file_path=Path(f"/skills/{name}/SKILL.md"),
        base_dir=Path(f"/skills/{name}"),
        source_info=SourceInfo(path=f"/skills/{name}/SKILL.md", source="user"),
        disable_model_invocation=disabled,
    )


# ── PromptTemplate ────────────────────────────────────────────────────────────

class TestPromptTemplateDefault:
    def test_contains_cwd(self):
        out = PromptTemplate(cwd="/my/project").build()
        assert "/my/project" in out

    def test_contains_today_date(self):
        out = PromptTemplate(cwd="/project").build()
        assert date.today().isoformat() in out

    def test_default_base_text(self):
        out = PromptTemplate(cwd="/project").build()
        assert "You are a helpful assistant." in out

    def test_windows_path_normalized(self):
        out = PromptTemplate(cwd=r"C:\Users\joe\project").build()
        assert "\\" not in out

    def test_guidelines_included(self):
        out = PromptTemplate(cwd="/project", prompt_guidelines=["Always explain your reasoning"]).build()
        assert "Always explain your reasoning" in out

    def test_append_system_prompt_included(self):
        out = PromptTemplate(cwd="/project", append_system_prompt="EXTRA INSTRUCTIONS").build()
        assert "EXTRA INSTRUCTIONS" in out

    def test_no_read_tool_excludes_skills(self):
        skill = make_skill("my-skill")
        out = PromptTemplate(cwd="/project", tools=[make_tool("bash")], skills=[skill]).build()
        assert "my-skill" not in out

    def test_read_tool_includes_skills(self):
        skill = make_skill("my-skill")
        out = PromptTemplate(cwd="/project", tools=[make_tool("read")], skills=[skill]).build()
        assert "my-skill" in out


class TestPromptTemplateCustomPrompt:
    def test_uses_custom_prompt_as_base(self):
        out = PromptTemplate(cwd="/project", custom_prompt="MY CUSTOM PROMPT").build()
        assert "MY CUSTOM PROMPT" in out
        assert "You are a helpful assistant." not in out

    def test_custom_prompt_still_gets_footer(self):
        out = PromptTemplate(cwd="/project", custom_prompt="CUSTOM").build()
        assert date.today().isoformat() in out

    def test_custom_prompt_with_append(self):
        out = PromptTemplate(cwd="/project", custom_prompt="BASE", append_system_prompt="APPENDED").build()
        assert "BASE" in out
        assert "APPENDED" in out


# ── build_system_prompt (backward compat wrapper) ─────────────────────────────

class TestBuildSystemPrompt:
    def test_delegates_to_prompt_template(self):
        options = SystemPromptOptions(cwd="/project", prompt_guidelines=["Be concise"])
        out = build_system_prompt(options)
        assert "Be concise" in out
        assert "/project" in out


# ── build_guidelines ──────────────────────────────────────────────────────────

class TestBuildGuidelines:
    def test_formats_as_bullet_points(self):
        out = build_guidelines(["Be helpful", "Be concise"])
        assert "- Be helpful" in out
        assert "- Be concise" in out

    def test_empty_list_returns_empty_string(self):
        assert build_guidelines([]) == ""

    def test_strips_whitespace(self):
        out = build_guidelines(["  trimmed  "])
        assert "- trimmed" in out

    def test_skips_blank_entries(self):
        out = build_guidelines(["", "  ", "real guideline"])
        assert "real guideline" in out
        assert out.count("-") == 1


# ── format_skills_for_prompt ──────────────────────────────────────────────────

class TestFormatSkillsForPrompt:
    def test_empty_list_returns_empty_string(self):
        assert format_skills_for_prompt([]) == ""

    def test_disabled_skill_excluded(self):
        skill = make_skill("hidden", disabled=True)
        assert format_skills_for_prompt([skill]) == ""

    def test_includes_skill_name_and_description(self):
        skill = make_skill("my-skill", description="Does great things")
        out = format_skills_for_prompt([skill])
        assert "my-skill" in out
        assert "Does great things" in out

    def test_skill_view_instruction_present(self):
        skill = make_skill("my-skill")
        out = format_skills_for_prompt([skill])
        assert "skill" in out

    def test_xml_special_chars_escaped(self):
        skill = make_skill("skill", description="Use <tags> & 'quotes'")
        out = format_skills_for_prompt([skill])
        assert "<tags>" not in out
        assert "&lt;tags&gt;" in out

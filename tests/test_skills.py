"""Tests for Skills — list, load, metadata, summary, formatter stripping."""

from pathlib import Path

from operator_use.agent.skills.service import Skills


def make_skill(base: Path, name: str, content: str, source: str = "workspace") -> Path:
    """Helper to create a skill directory with a SKILL.md file."""
    if source == "workspace":
        skill_dir = base / "skills" / name
    else:
        skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


# --- list_skills ---


def test_list_skills_empty(tmp_path):
    skills = Skills(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin")
    assert skills.list_skills() == []


def test_list_workspace_skills(tmp_path):
    make_skill(tmp_path, "my-skill", "# My Skill")
    skills = Skills(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin")
    result = skills.list_skills()
    assert any(s["name"] == "my-skill" and s["source"] == "workspace" for s in result)


def test_list_builtin_skills(tmp_path):
    builtin_dir = tmp_path / "builtin"
    make_skill(builtin_dir, "builtin-skill", "# Builtin", source="builtin")
    skills = Skills(workspace=tmp_path, builtin_skills_dir=builtin_dir)
    result = skills.list_skills()
    assert any(s["name"] == "builtin-skill" and s["source"] == "builtin" for s in result)


def test_list_skills_ignores_non_dirs(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "not-a-dir.txt").write_text("ignored")
    skills = Skills(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin")
    assert skills.list_skills() == []


def test_list_skills_ignores_dirs_without_skill_md(tmp_path):
    skill_dir = tmp_path / "skills" / "no-md"
    skill_dir.mkdir(parents=True)
    (skill_dir / "README.txt").write_text("no SKILL.md here")
    skills = Skills(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin")
    assert skills.list_skills() == []


def test_list_skills_both_sources(tmp_path):
    builtin_dir = tmp_path / "builtin"
    make_skill(tmp_path, "ws-skill", "# WS")
    make_skill(builtin_dir, "bt-skill", "# BT", source="builtin")
    skills = Skills(workspace=tmp_path, builtin_skills_dir=builtin_dir)
    result = skills.list_skills()
    names = [s["name"] for s in result]
    assert "ws-skill" in names
    assert "bt-skill" in names


# --- load_skill_content ---


def test_load_skill_content_workspace(tmp_path):
    make_skill(tmp_path, "greet", "Hello from workspace skill.")
    skills = Skills(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin")
    assert skills.load_skill_content("greet") == "Hello from workspace skill."


def test_load_skill_content_builtin_fallback(tmp_path):
    builtin_dir = tmp_path / "builtin"
    make_skill(builtin_dir, "fallback", "Builtin content.", source="builtin")
    skills = Skills(workspace=tmp_path, builtin_skills_dir=builtin_dir)
    assert skills.load_skill_content("fallback") == "Builtin content."


def test_load_skill_content_workspace_takes_priority(tmp_path):
    builtin_dir = tmp_path / "builtin"
    make_skill(tmp_path, "shared", "workspace version")
    make_skill(builtin_dir, "shared", "builtin version", source="builtin")
    skills = Skills(workspace=tmp_path, builtin_skills_dir=builtin_dir)
    assert skills.load_skill_content("shared") == "workspace version"


def test_load_skill_content_not_found_returns_none(tmp_path):
    skills = Skills(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin")
    assert skills.load_skill_content("ghost") is None


# --- _strip_skill_formatter ---


def test_strip_formatter_with_frontmatter(tmp_path):
    skills = Skills(workspace=tmp_path)
    content = "---\nname: test\ndescription: A skill\n---\nActual content here."
    assert skills._strip_skill_formatter(content) == "Actual content here."


def test_strip_formatter_no_frontmatter(tmp_path):
    skills = Skills(workspace=tmp_path)
    assert skills._strip_skill_formatter("No frontmatter.") == "No frontmatter."


def test_strip_formatter_preserves_body(tmp_path):
    skills = Skills(workspace=tmp_path)
    content = "---\nname: foo\n---\n## Instructions\nDo the thing."
    result = skills._strip_skill_formatter(content)
    assert "## Instructions" in result
    assert "Do the thing." in result


# --- load_skill ---


def test_load_skill_wraps_with_header(tmp_path):
    make_skill(tmp_path, "helper", "Do something useful.")
    skills = Skills(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin")
    result = skills.load_skill("helper")
    assert result.startswith("### Skill: helper")
    assert "Do something useful." in result


def test_load_skill_not_found_returns_none(tmp_path):
    skills = Skills(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin")
    assert skills.load_skill("nonexistent") is None


def test_load_skill_strips_frontmatter(tmp_path):
    make_skill(tmp_path, "fmskill", "---\nname: fmskill\n---\nThe actual skill body.")
    skills = Skills(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin")
    result = skills.load_skill("fmskill")
    assert "name: fmskill" not in result
    assert "The actual skill body." in result


# --- load_skills_for_context ---


def test_load_skills_for_context_multiple(tmp_path):
    make_skill(tmp_path, "skill-a", "Content A.")
    make_skill(tmp_path, "skill-b", "Content B.")
    skills = Skills(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin")
    result = skills.load_skills_for_context(["skill-a", "skill-b"])
    assert "Content A." in result
    assert "Content B." in result
    assert "---" in result


def test_load_skills_for_context_skips_missing(tmp_path):
    make_skill(tmp_path, "real-skill", "Real content.")
    skills = Skills(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin")
    result = skills.load_skills_for_context(["real-skill", "ghost-skill"])
    assert "Real content." in result


def test_load_skills_for_context_empty_list(tmp_path):
    skills = Skills(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin")
    assert skills.load_skills_for_context([]) == ""


# --- get_skill_metadata ---


def test_get_skill_metadata_parses_frontmatter(tmp_path):
    content = "---\nname: my-skill\ndescription: Does great things\nauthor: dev\n---\nBody."
    make_skill(tmp_path, "meta-skill", content)
    skills = Skills(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin")
    meta = skills.get_skill_metadata("meta-skill")
    assert meta["name"] == "my-skill"
    assert meta["description"] == "Does great things"
    assert meta["author"] == "dev"


def test_get_skill_metadata_no_frontmatter(tmp_path):
    make_skill(tmp_path, "no-meta", "Just plain content.")
    skills = Skills(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin")
    assert skills.get_skill_metadata("no-meta") == {}


def test_get_skill_metadata_not_found(tmp_path):
    skills = Skills(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin")
    assert skills.get_skill_metadata("ghost") == {}


def test_get_skill_metadata_value_with_colon(tmp_path):
    content = "---\nurl: https://example.com\n---\nBody."
    make_skill(tmp_path, "colon-skill", content)
    skills = Skills(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin")
    meta = skills.get_skill_metadata("colon-skill")
    assert meta["url"] == "https://example.com"


# --- build_skills_summary ---


def test_build_skills_summary_empty(tmp_path):
    skills = Skills(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin")
    assert skills.build_skills_summary() == ""


def test_build_skills_summary_includes_name_and_description(tmp_path):
    content = "---\ndescription: Sends emails\n---\nDo stuff."
    make_skill(tmp_path, "email-skill", content)
    skills = Skills(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin")
    result = skills.build_skills_summary()
    assert "email-skill" in result
    assert "Sends emails" in result


def test_build_skills_summary_includes_path(tmp_path):
    make_skill(tmp_path, "path-skill", "# Path skill")
    skills = Skills(workspace=tmp_path, builtin_skills_dir=tmp_path / "builtin")
    result = skills.build_skills_summary()
    assert "path-skill" in result

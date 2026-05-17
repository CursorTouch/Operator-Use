"""Tests for skill loader: frontmatter parsing, validation, load_skill_from_file, load_skills."""
import pytest
from pathlib import Path

from program.skill.loader import (
    parse_frontmatter,
    validate_name,
    validate_description,
    load_skill_from_file,
    load_skills_from_dir,
)
from program.prompt.utils import format_skills_for_prompt
from program.skill.types import LoadSkillsOptions, Skill


# ── parse_frontmatter ─────────────────────────────────────────────────────────

class TestParseFrontmatter:
    def test_parses_name_and_description(self):
        content = "---\nname: my-skill\ndescription: Does something\n---\nbody"
        data, body = parse_frontmatter(content)
        assert data['name'] == 'my-skill'
        assert data['description'] == 'Does something'
        assert body == 'body'

    def test_no_frontmatter_returns_empty_dict(self):
        data, body = parse_frontmatter("just body text")
        assert data == {}
        assert body == "just body text"

    def test_bool_values_parsed(self):
        content = "---\ndisable-model-invocation: true\n---\n"
        data, _ = parse_frontmatter(content)
        assert data['disable-model-invocation'] is True

    def test_quoted_strings_stripped(self):
        content = '---\nname: "quoted"\n---\n'
        data, _ = parse_frontmatter(content)
        assert data['name'] == 'quoted'

    def test_single_quoted_strings_stripped(self):
        content = "---\nname: 'single'\n---\n"
        data, _ = parse_frontmatter(content)
        assert data['name'] == 'single'


# ── validate_name ─────────────────────────────────────────────────────────────

class TestValidateName:
    def test_valid_name(self):
        assert validate_name('my-skill', 'my-skill') == []

    def test_name_mismatch_with_dir(self):
        errors = validate_name('other', 'my-skill')
        assert any('does not match' in e for e in errors)

    def test_uppercase_invalid(self):
        errors = validate_name('MySkill', 'MySkill')
        assert any('invalid characters' in e for e in errors)

    def test_leading_hyphen_invalid(self):
        errors = validate_name('-bad', '-bad')
        assert any('start' in e for e in errors)

    def test_trailing_hyphen_invalid(self):
        errors = validate_name('bad-', 'bad-')
        assert any('end' in e for e in errors)

    def test_double_hyphen_invalid(self):
        errors = validate_name('a--b', 'a--b')
        assert any('consecutive' in e for e in errors)

    def test_too_long(self):
        long = 'a' * 65
        errors = validate_name(long, long)
        assert any('exceeds' in e for e in errors)


# ── validate_description ─────────────────────────────────────────────────────

class TestValidateDescription:
    def test_valid_description(self):
        assert validate_description("A good description") == []

    def test_none_is_invalid(self):
        assert validate_description(None) != []

    def test_empty_string_invalid(self):
        assert validate_description("") != []

    def test_too_long(self):
        errors = validate_description('x' * 1025)
        assert errors != []


# ── load_skill_from_file ──────────────────────────────────────────────────────

class TestLoadSkillFromFile:
    def _write(self, tmp_path: Path, dir_name: str, content: str) -> Path:
        d = tmp_path / dir_name
        d.mkdir()
        f = d / "SKILL.md"
        f.write_text(content)
        return f

    def test_loads_valid_skill(self, tmp_path):
        f = self._write(tmp_path, "my-skill", "---\nname: my-skill\ndescription: Does something\n---\nbody")
        skill, diags = load_skill_from_file(f, source='user')
        assert skill is not None
        assert skill.name == 'my-skill'
        assert skill.description == 'Does something'

    def test_missing_description_returns_none(self, tmp_path):
        f = self._write(tmp_path, "no-desc", "---\nname: no-desc\n---\nbody")
        skill, diags = load_skill_from_file(f, source='user')
        assert skill is None
        assert any('description' in d.message for d in diags)

    def test_source_info_set(self, tmp_path):
        f = self._write(tmp_path, "src-skill", "---\nname: src-skill\ndescription: Test\n---\n")
        skill, _ = load_skill_from_file(f, source='project')
        assert skill is not None
        assert skill.source_info.source == 'project'

    def test_disable_model_invocation_flag(self, tmp_path):
        f = self._write(tmp_path, "silent", "---\nname: silent\ndescription: Silent skill\ndisable-model-invocation: true\n---\n")
        skill, _ = load_skill_from_file(f, source='user')
        assert skill is not None
        assert skill.disable_model_invocation is True

    def test_name_mismatch_produces_warning(self, tmp_path):
        f = self._write(tmp_path, "my-skill", "---\nname: other-name\ndescription: Something\n---\n")
        skill, diags = load_skill_from_file(f, source='user')
        assert any('does not match' in d.message for d in diags)


# ── load_skills_from_dir ──────────────────────────────────────────────────────

class TestLoadSkillsFromDir:
    def test_loads_skill_in_subdirectory(self, tmp_path):
        sub = tmp_path / "my-skill"
        sub.mkdir()
        (sub / "SKILL.md").write_text("---\nname: my-skill\ndescription: Does things\n---\n")
        result = load_skills_from_dir(tmp_path, 'user')
        assert len(result.skills) == 1
        assert result.skills[0].name == 'my-skill'

    def test_skips_hidden_directories(self, tmp_path):
        hidden = tmp_path / ".hidden-skill"
        hidden.mkdir()
        (hidden / "SKILL.md").write_text("---\nname: .hidden-skill\ndescription: Hidden\n---\n")
        result = load_skills_from_dir(tmp_path, 'user')
        assert len(result.skills) == 0

    def test_multiple_skills_loaded(self, tmp_path):
        for name in ['skill-a', 'skill-b', 'skill-c']:
            d = tmp_path / name
            d.mkdir()
            (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Skill {name}\n---\n")
        result = load_skills_from_dir(tmp_path, 'user')
        assert len(result.skills) == 3

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        result = load_skills_from_dir(tmp_path / "missing", 'user')
        assert result.skills == []

    def test_skill_file_at_root_loaded(self, tmp_path):
        # If SKILL.md at root, treat this dir as the skill
        (tmp_path / "SKILL.md").write_text("---\nname: root-level\ndescription: Root skill\n---\n")
        result = load_skills_from_dir(tmp_path.parent, 'user')
        # root SKILL.md takes over for this directory
        root_result = load_skills_from_dir(tmp_path, 'user')
        assert len(root_result.skills) == 1


# ── format_skills_for_prompt ──────────────────────────────────────────────────

class TestFormatSkillsForPrompt:
    def _make_skill(self, name: str, description: str, disable: bool = False) -> Skill:
        from program.skill.types import SourceInfo
        return Skill(
            name=name,
            description=description,
            file_path=Path(f"/skills/{name}/SKILL.md"),
            base_dir=Path(f"/skills/{name}"),
            source_info=SourceInfo(path=f"/skills/{name}/SKILL.md", source='user'),
            disable_model_invocation=disable,
        )

    def test_empty_list_returns_empty_string(self):
        assert format_skills_for_prompt([]) == ''

    def test_all_disabled_returns_empty(self):
        skills = [self._make_skill('s', 'desc', disable=True)]
        assert format_skills_for_prompt(skills) == ''

    def test_skill_name_in_output(self):
        skills = [self._make_skill('my-skill', 'Does something')]
        output = format_skills_for_prompt(skills)
        assert 'my-skill' in output

    def test_skill_description_in_output(self):
        skills = [self._make_skill('test', 'Test description')]
        output = format_skills_for_prompt(skills)
        assert 'Test description' in output

    def test_xml_tags_present(self):
        skills = [self._make_skill('s', 'desc')]
        output = format_skills_for_prompt(skills)
        assert '<available_skills>' in output
        assert '</available_skills>' in output

    def test_special_chars_escaped(self):
        skills = [self._make_skill('safe', 'Has <brackets> & "quotes"')]
        output = format_skills_for_prompt(skills)
        assert '<brackets>' not in output
        assert '&lt;brackets&gt;' in output

    def test_disabled_skill_excluded(self):
        skills = [
            self._make_skill('visible', 'Visible'),
            self._make_skill('hidden', 'Hidden', disable=True),
        ]
        output = format_skills_for_prompt(skills)
        assert 'visible' in output
        assert 'hidden' not in output

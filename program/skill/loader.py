from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from program.settings.paths import get_skills_dir
from program.skill.types import (
    CollisionInfo, LoadSkillsOptions, LoadSkillsResult,
    ResourceDiagnostic, Skill, SkillFrontmatter, SourceInfo,
)

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
SKILL_FILE_NAME = "SKILL.md"
IGNORE_FILE_NAMES = [".gitignore", ".ignore", ".fdignore"]


# ============================================================================
# Frontmatter parsing
# ============================================================================

def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Extract YAML-style frontmatter from markdown content."""
    pattern = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)
    match = pattern.match(content)
    if not match:
        return {}, content

    data: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if ':' not in stripped:
            continue
        key, _, raw_value = stripped.partition(':')
        key = key.strip()
        value: Any = raw_value.strip()
        if isinstance(value, str):
            if (value.startswith('"') and value.endswith('"')) or \
               (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            elif value.lower() == 'true':
                value = True
            elif value.lower() == 'false':
                value = False
        data[key] = value

    return data, content[match.end():]


# ============================================================================
# Validation
# ============================================================================

def validate_name(name: str, parent_dir_name: str) -> list[str]:
    errors: list[str] = []
    if name != parent_dir_name:
        errors.append(f'name "{name}" does not match parent directory "{parent_dir_name}"')
    if len(name) > MAX_NAME_LENGTH:
        errors.append(f'name exceeds {MAX_NAME_LENGTH} characters ({len(name)})')
    if not re.match(r'^[a-z0-9-]+$', name):
        errors.append('name contains invalid characters (must be lowercase a-z, 0-9, hyphens only)')
    if name.startswith('-') or name.endswith('-'):
        errors.append('name must not start or end with a hyphen')
    if '--' in name:
        errors.append('name must not contain consecutive hyphens')
    return errors


def validate_description(description: str | None) -> list[str]:
    if not description or not description.strip():
        return ['description is required']
    if len(description) > MAX_DESCRIPTION_LENGTH:
        return [f'description exceeds {MAX_DESCRIPTION_LENGTH} characters ({len(description)})']
    return []


# ============================================================================
# Source info construction
# ============================================================================

def _make_source_info(file_path: Path, base_dir: Path, source: str) -> SourceInfo:
    scope = source if source in ('user', 'project') else None
    return SourceInfo(
        path=str(file_path),
        source=source,
        scope=scope,
        base_dir=str(base_dir),
    )


# ============================================================================
# Single-file loading
# ============================================================================

def load_skill_from_file(
    file_path: Path,
    source: str,
) -> tuple[Skill | None, list[ResourceDiagnostic]]:
    diagnostics: list[ResourceDiagnostic] = []

    try:
        raw = file_path.read_text(encoding='utf-8')
        front, _ = parse_frontmatter(raw)

        skill_dir = file_path.parent
        parent_dir_name = skill_dir.name

        description = front.get('description')
        for error in validate_description(description):
            diagnostics.append(ResourceDiagnostic(type='warning', message=error, path=str(file_path)))

        name = str(front.get('name') or parent_dir_name)
        for error in validate_name(name, parent_dir_name):
            diagnostics.append(ResourceDiagnostic(type='warning', message=error, path=str(file_path)))

        if not description or not description.strip():
            return None, diagnostics

        disable = bool(front.get('disable-model-invocation', False))

        skill = Skill(
            name=name,
            description=description,
            file_path=file_path,
            base_dir=skill_dir,
            source_info=_make_source_info(file_path, skill_dir, source),
            disable_model_invocation=disable,
        )
        return skill, diagnostics

    except Exception as exc:
        diagnostics.append(ResourceDiagnostic(
            type='warning',
            message=str(exc),
            path=str(file_path),
        ))
        return None, diagnostics


# ============================================================================
# Directory scanning
# ============================================================================

def _load_from_dir_internal(
    dir_path: Path,
    source: str,
    include_root_files: bool,
) -> LoadSkillsResult:
    skills: list[Skill] = []
    diagnostics: list[ResourceDiagnostic] = []

    if not dir_path.is_dir():
        return LoadSkillsResult(skills=skills, diagnostics=diagnostics)

    try:
        entries = list(dir_path.iterdir())
    except OSError:
        return LoadSkillsResult(skills=skills, diagnostics=diagnostics)

    # If SKILL.md is present at this level, treat this directory as the skill root
    skill_file = dir_path / SKILL_FILE_NAME
    if skill_file.is_file():
        skill, diags = load_skill_from_file(skill_file, source)
        if skill:
            skills.append(skill)
        diagnostics.extend(diags)
        return LoadSkillsResult(skills=skills, diagnostics=diagnostics)

    for entry in sorted(entries, key=lambda e: e.name):
        if entry.name.startswith('.') or entry.name == '__pycache__':
            continue

        if entry.is_dir():
            sub = _load_from_dir_internal(entry, source, include_root_files=False)
            skills.extend(sub.skills)
            diagnostics.extend(sub.diagnostics)
        elif include_root_files and entry.is_file() and entry.suffix == '.md':
            skill, diags = load_skill_from_file(entry, source)
            if skill:
                skills.append(skill)
            diagnostics.extend(diags)

    return LoadSkillsResult(skills=skills, diagnostics=diagnostics)


def load_skills_from_dir(dir_path: Path, source: str) -> LoadSkillsResult:
    return _load_from_dir_internal(dir_path, source, include_root_files=True)


# ============================================================================
# Full skill loading (all configured sources)
# ============================================================================

def load_skills(options: LoadSkillsOptions) -> LoadSkillsResult:
    cwd = options.cwd
    all_diagnostics: list[ResourceDiagnostic] = []
    collision_diagnostics: list[ResourceDiagnostic] = []

    skill_map: dict[str, Skill] = {}
    seen_real_paths: set[str] = set()

    def add_skills(result: LoadSkillsResult) -> None:
        all_diagnostics.extend(result.diagnostics)
        for skill in result.skills:
            real = str(skill.file_path.resolve())
            if real in seen_real_paths:
                continue
            existing = skill_map.get(skill.name)
            if existing:
                collision_diagnostics.append(ResourceDiagnostic(
                    type='collision',
                    message=f'name "{skill.name}" collision',
                    path=str(skill.file_path),
                    collision=CollisionInfo(
                        resource_type='skill',
                        name=skill.name,
                        winner_path=str(existing.file_path),
                        loser_path=str(skill.file_path),
                    ),
                ))
            else:
                skill_map[skill.name] = skill
                seen_real_paths.add(real)

    if options.include_defaults:
        add_skills(_load_from_dir_internal(get_skills_dir(), 'user', True))
        add_skills(_load_from_dir_internal(get_skills_dir(cwd), 'project', True))

    for raw_path in options.skill_paths:
        resolved = Path(raw_path).expanduser()
        if not resolved.is_absolute():
            resolved = (cwd / resolved).resolve()

        if not resolved.exists():
            all_diagnostics.append(ResourceDiagnostic(
                type='warning',
                message='skill path does not exist',
                path=str(resolved),
            ))
            continue

        try:
            if resolved.is_dir():
                add_skills(_load_from_dir_internal(resolved, 'path', True))
            elif resolved.is_file() and resolved.suffix == '.md':
                skill, diags = load_skill_from_file(resolved, 'path')
                add_skills(LoadSkillsResult(
                    skills=[skill] if skill else [],
                    diagnostics=diags,
                ))
            else:
                all_diagnostics.append(ResourceDiagnostic(
                    type='warning',
                    message='skill path is not a markdown file or directory',
                    path=str(resolved),
                ))
        except Exception as exc:
            all_diagnostics.append(ResourceDiagnostic(
                type='warning',
                message=str(exc),
                path=str(resolved),
            ))

    return LoadSkillsResult(
        skills=list(skill_map.values()),
        diagnostics=[*all_diagnostics, *collision_diagnostics],
    )


# ============================================================================
# System prompt formatting
# ============================================================================

def _escape_xml(text: str) -> str:
    return (
        text.replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&apos;')
    )


def format_skills_for_prompt(skills: list[Skill]) -> str:
    visible = [s for s in skills if not s.disable_model_invocation]
    if not visible:
        return ''

    lines = [
        '',
        '',
        'The following skills provide specialized instructions for specific tasks.',
        'Use the read tool to load a skill\'s file when the task matches its description.',
        'When a skill file references a relative path, resolve it against the skill directory '
        '(parent of SKILL.md / dirname of the path) and use that absolute path in tool commands.',
        '',
        '<available_skills>',
    ]

    for skill in visible:
        lines.append('  <skill>')
        lines.append(f'    <name>{_escape_xml(skill.name)}</name>')
        lines.append(f'    <description>{_escape_xml(skill.description)}</description>')
        lines.append(f'    <location>{_escape_xml(str(skill.file_path))}</location>')
        lines.append('  </skill>')

    lines.append('</available_skills>')
    return '\n'.join(lines)

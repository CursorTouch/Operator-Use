from __future__ import annotations

import os
import re
from os.path import basename, dirname, isabs, join, realpath, sep
from typing import Optional

from program.skill.types import LoadSkillsResult, Skill

_MAX_NAME_LENGTH = 64
_MAX_DESCRIPTION_LENGTH = 1024
_SKILL_FILE = "SKILL.md"


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> dict:
    """Parse a YAML-like frontmatter block (---...---) into a flat dict."""
    match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?", text, re.DOTALL)
    if not match:
        return {}
    block = match.group(1)
    result: dict = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        # Coerce booleans
        if value.lower() == "true":
            result[key] = True
        elif value.lower() == "false":
            result[key] = False
        else:
            # Strip surrounding quotes
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_name(name: str, parent_dir_name: str) -> list[str]:
    errors: list[str] = []
    if name != parent_dir_name:
        errors.append(f'name "{name}" does not match parent directory "{parent_dir_name}"')
    if len(name) > _MAX_NAME_LENGTH:
        errors.append(f"name exceeds {_MAX_NAME_LENGTH} characters ({len(name)})")
    if not re.fullmatch(r"[a-z0-9-]+", name):
        errors.append("name contains invalid characters (must be lowercase a-z, 0-9, hyphens only)")
    if name.startswith("-") or name.endswith("-"):
        errors.append("name must not start or end with a hyphen")
    if "--" in name:
        errors.append("name must not contain consecutive hyphens")
    return errors


def _validate_description(description: Optional[str]) -> list[str]:
    if not description or not description.strip():
        return ["description is required"]
    if len(description) > _MAX_DESCRIPTION_LENGTH:
        return [f"description exceeds {_MAX_DESCRIPTION_LENGTH} characters ({len(description)})"]
    return []


# ---------------------------------------------------------------------------
# File loader
# ---------------------------------------------------------------------------

def load_skill_from_file(
    file_path: str, source: str
) -> tuple[Optional[Skill], list[dict]]:
    diagnostics: list[dict] = []
    try:
        with open(file_path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, [{"type": "warning", "message": str(exc), "path": file_path}]

    frontmatter = _parse_frontmatter(raw)
    skill_dir = dirname(file_path)
    parent_dir_name = basename(skill_dir)

    description = frontmatter.get("description") or ""
    name = frontmatter.get("name") or parent_dir_name

    for err in _validate_description(description):
        diagnostics.append({"type": "warning", "message": err, "path": file_path})
    for err in _validate_name(name, parent_dir_name):
        diagnostics.append({"type": "warning", "message": err, "path": file_path})

    # Skip entirely if description is missing
    if not description or not description.strip():
        return None, diagnostics

    skill = Skill(
        name=name,
        description=description,
        file_path=file_path,
        base_dir=skill_dir,
        disable_model_invocation=bool(frontmatter.get("disable-model-invocation", False)),
    )
    return skill, diagnostics


# ---------------------------------------------------------------------------
# Directory loader
# ---------------------------------------------------------------------------

def _load_from_dir(
    directory: str,
    source: str,
    include_root_files: bool,
) -> LoadSkillsResult:
    skills: list[Skill] = []
    diagnostics: list[dict] = []

    if not os.path.isdir(directory):
        return LoadSkillsResult()

    try:
        entries = list(os.scandir(directory))
    except OSError as exc:
        return LoadSkillsResult(diagnostics=[{"type": "warning", "message": str(exc), "path": directory}])

    # If SKILL.md exists in this directory, treat the whole dir as one skill
    for entry in entries:
        if entry.name == _SKILL_FILE and entry.is_file(follow_symlinks=True):
            skill, diags = load_skill_from_file(entry.path, source)
            diagnostics.extend(diags)
            if skill:
                skills.append(skill)
            return LoadSkillsResult(skills=skills, diagnostics=diagnostics)

    for entry in sorted(entries, key=lambda e: e.name):
        if entry.name.startswith(".") or entry.name == "node_modules":
            continue

        is_dir = entry.is_dir(follow_symlinks=True)
        is_file = entry.is_file(follow_symlinks=True)

        if is_dir:
            sub = _load_from_dir(entry.path, source, include_root_files=False)
            skills.extend(sub.skills)
            diagnostics.extend(sub.diagnostics)
        elif is_file and include_root_files and entry.name.endswith(".md"):
            skill, diags = load_skill_from_file(entry.path, source)
            diagnostics.extend(diags)
            if skill:
                skills.append(skill)

    return LoadSkillsResult(skills=skills, diagnostics=diagnostics)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def load_skills(
    cwd: str,
    agent_dir: str,
    skill_paths: list[str],
    include_defaults: bool = False,
) -> LoadSkillsResult:
    skill_map: dict[str, Skill] = {}
    real_path_set: set[str] = set()
    all_diagnostics: list[dict] = []
    collision_diagnostics: list[dict] = []

    user_skills_dir = join(agent_dir, "skills")
    project_skills_dir = join(cwd, ".pi", "skills")

    def _get_source(resolved: str) -> str:
        if _is_under(resolved, user_skills_dir):
            return "user"
        if _is_under(resolved, project_skills_dir):
            return "project"
        return "path"

    def _add(result: LoadSkillsResult) -> None:
        all_diagnostics.extend(result.diagnostics)
        for skill in result.skills:
            rp = realpath(skill.file_path)
            if rp in real_path_set:
                continue
            if skill.name in skill_map:
                collision_diagnostics.append({
                    "type": "collision",
                    "message": f'name "{skill.name}" collision',
                    "path": skill.file_path,
                    "collision": {
                        "resource_type": "skill",
                        "name": skill.name,
                        "winner_path": skill_map[skill.name].file_path,
                        "loser_path": skill.file_path,
                    },
                })
            else:
                skill_map[skill.name] = skill
                real_path_set.add(rp)

    if include_defaults:
        _add(_load_from_dir(user_skills_dir, "user", include_root_files=True))
        _add(_load_from_dir(project_skills_dir, "project", include_root_files=True))

    for raw_path in skill_paths:
        resolved = _resolve(raw_path, cwd)
        if not os.path.exists(resolved):
            all_diagnostics.append({"type": "warning", "message": "skill path does not exist", "path": resolved})
            continue
        source = _get_source(resolved)
        try:
            if os.path.isdir(resolved):
                _add(_load_from_dir(resolved, source, include_root_files=True))
            elif resolved.endswith(".md"):
                skill, diags = load_skill_from_file(resolved, source)
                all_diagnostics.extend(diags)
                if skill:
                    _add(LoadSkillsResult(skills=[skill]))
            else:
                all_diagnostics.append({"type": "warning", "message": "skill path is not a markdown file", "path": resolved})
        except OSError as exc:
            all_diagnostics.append({"type": "warning", "message": str(exc), "path": resolved})

    return LoadSkillsResult(
        skills=list(skill_map.values()),
        diagnostics=all_diagnostics + collision_diagnostics,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve(p: str, cwd: str) -> str:
    p = p.strip()
    if p == "~":
        return os.path.expanduser("~")
    if p.startswith("~/") or p.startswith("~" + sep):
        return os.path.join(os.path.expanduser("~"), p[2:])
    return p if isabs(p) else os.path.normpath(join(cwd, p))


def _is_under(target: str, root: str) -> bool:
    root = os.path.normpath(root)
    return target == root or target.startswith(root + sep)

"""Named subagent profiles — loaded from SUBAGENT.md files.

Each profile defines a specialized subagent with a fixed system prompt and
an optional allow-list of tools. Profiles are discovered from builtins,
the user's global directory, and the active project directory.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from program.diagnostics.types import ResourceDiagnostic

SUBAGENT_FILE_NAME = 'SUBAGENT.md'
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024


class SubagentProfile(BaseModel):
    name: str
    description: str
    tools: list[str]        # allowed tool names; empty list = all tools
    system_prompt: str
    file_path: Path

    model_config = {'arbitrary_types_allowed': True}


class LoadProfilesResult(BaseModel):
    profiles: list[SubagentProfile] = Field(default_factory=list)
    diagnostics: list[ResourceDiagnostic] = Field(default_factory=list)


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    pattern = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)
    match = pattern.match(content)
    if not match:
        return {}, content
    data: dict = {}
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if ':' not in stripped:
            continue
        key, _, raw_value = stripped.partition(':')
        key = key.strip()
        value: object = raw_value.strip()
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


def load_profile_from_file(file_path: Path) -> tuple[SubagentProfile | None, list[ResourceDiagnostic]]:
    diagnostics: list[ResourceDiagnostic] = []
    try:
        raw = file_path.read_text(encoding='utf-8')
        front, body = _parse_frontmatter(raw)

        parent_dir_name = file_path.parent.name
        name = str(front.get('name') or parent_dir_name)
        description = str(front.get('description', '')).strip()
        tools_raw = str(front.get('tools', '')).strip()
        tools = [t.strip() for t in tools_raw.split(',') if t.strip()] if tools_raw else []

        if not description:
            diagnostics.append(ResourceDiagnostic(
                type='warning', message='description is required', path=str(file_path),
            ))
            return None, diagnostics

        if len(name) > MAX_NAME_LENGTH:
            diagnostics.append(ResourceDiagnostic(
                type='warning',
                message=f'name exceeds {MAX_NAME_LENGTH} characters',
                path=str(file_path),
            ))

        profile = SubagentProfile(
            name=name,
            description=description,
            tools=tools,
            system_prompt=body.strip(),
            file_path=file_path,
        )
        return profile, diagnostics

    except Exception as exc:
        diagnostics.append(ResourceDiagnostic(type='warning', message=str(exc), path=str(file_path)))
        return None, diagnostics


def _load_from_dir(dir_path: Path) -> LoadProfilesResult:
    profiles: list[SubagentProfile] = []
    diagnostics: list[ResourceDiagnostic] = []

    if not dir_path.is_dir():
        return LoadProfilesResult(profiles=profiles, diagnostics=diagnostics)

    try:
        entries = list(dir_path.iterdir())
    except OSError:
        return LoadProfilesResult(profiles=profiles, diagnostics=diagnostics)

    subagent_file = dir_path / SUBAGENT_FILE_NAME
    if subagent_file.is_file():
        profile, diags = load_profile_from_file(subagent_file)
        diagnostics.extend(diags)
        if profile:
            profiles.append(profile)
        return LoadProfilesResult(profiles=profiles, diagnostics=diagnostics)

    for entry in sorted(entries, key=lambda e: e.name):
        if entry.name.startswith('.') or entry.name == '__pycache__':
            continue
        if entry.is_dir():
            sub = _load_from_dir(entry)
            profiles.extend(sub.profiles)
            diagnostics.extend(sub.diagnostics)

    return LoadProfilesResult(profiles=profiles, diagnostics=diagnostics)


def load_profiles(dirs: list[Path]) -> LoadProfilesResult:
    """Load profiles from multiple directories; first-found wins on name collision."""
    seen: set[str] = set()
    all_profiles: list[SubagentProfile] = []
    all_diagnostics: list[ResourceDiagnostic] = []

    for dir_path in dirs:
        result = _load_from_dir(dir_path)
        all_diagnostics.extend(result.diagnostics)
        for profile in result.profiles:
            if profile.name not in seen:
                seen.add(profile.name)
                all_profiles.append(profile)

    return LoadProfilesResult(profiles=all_profiles, diagnostics=all_diagnostics)

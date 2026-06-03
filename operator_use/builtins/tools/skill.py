"""skill — view, create, edit, patch, and delete user skills."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


from operator_use.skill import usage as skill_usage
from operator_use.skill.curator import archive_skill
from operator_use.tool.types import Tool, ToolContext, ToolExecutionMode, ToolInvocation, ToolKind, ToolResult

_SUPPORT_PREFIXES = ('references/', 'templates/', 'scripts/')
_VALID_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,62}[a-z0-9]$|^[a-z0-9]$')
MAX_SKILL_SIZE = 15_000


class SkillSchema(BaseModel):
    action: Literal['view', 'create', 'edit', 'patch', 'delete', 'write_file', 'remove_file'] = Field(
        description=(
            'Action to perform:\n'
            '  view       — load the full SKILL.md content of a skill by name.\n'
            '  create     — create a new skill directory and SKILL.md.\n'
            '  edit       — fully replace the SKILL.md of an existing skill.\n'
            '  patch      — targeted find-and-replace within SKILL.md or a support file.\n'
            '  delete     — remove a skill entirely.\n'
            '  write_file — add/overwrite a support file (references/, templates/, scripts/).\n'
            '  remove_file — delete a support file.'
        )
    )
    name: str = Field(description='Skill name — lowercase, hyphens/underscores, max 64 chars.')
    content: str | None = Field(
        default=None,
        description='Full SKILL.md content. Required for create and edit.',
    )
    file_path: str | None = Field(
        default=None,
        description=(
            'Support file path relative to the skill directory. '
            'Must start with references/, templates/, or scripts/. '
            'Required for write_file and remove_file.'
        ),
    )
    file_content: str | None = Field(
        default=None,
        description='Content to write. Required for write_file.',
    )
    old_string: str | None = Field(
        default=None,
        description='Exact text to find. Required for patch.',
    )
    new_string: str | None = Field(
        default=None,
        description='Replacement text. Required for patch.',
    )
    replace_all: bool = Field(
        default=False,
        description='Replace all occurrences (patch only). Default replaces the first.',
    )

    @model_validator(mode='after')
    def _check_fields(self) -> 'SkillSchema':
        if self.action in {'create', 'edit'} and not self.content:
            raise ValueError(f"'content' is required for action='{self.action}'.")
        elif self.action == 'patch' and not self.old_string:
            raise ValueError("'old_string' is required for action='patch'.")
        elif self.action == 'write_file':
            missing = [f for f, v in [('file_path', self.file_path), ('file_content', self.file_content)] if not v]
            if missing:
                raise ValueError(f"{', '.join(repr(f) for f in missing)} required for action='write_file'.")
        elif self.action == 'remove_file' and not self.file_path:
            raise ValueError("'file_path' is required for action='remove_file'.")
        return self


def _validate_name(name: str) -> str | None:
    """Validate skill name format against alphanumeric pattern; return error message or None."""
    if not name or not _VALID_NAME_RE.match(name):
        return 'name must be lowercase letters, digits, hyphens, or underscores (2-64 chars)'
    return None


def _atomic_write(path: Path, text: str) -> None:
    """Write text to file atomically to prevent partial writes on crash."""
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    tmp.replace(path)


_NO_PROFILE_ERROR = "Skill operations require an active profile. Start the agent with --agent <profile>."


def _active_profile(context=None):
    """Resolve the active profile from the resource loader, matching other builtin tools."""
    loader = getattr(context, 'resource_loader', None)
    return getattr(loader, '_active_profile', None) if loader else None


def _skill_dir(name: str, context=None) -> Path | None:
    """Resolve the target skill directory from the context or active profile."""
    profile = _active_profile(context)
    if profile is None:
        return None
    return profile.skills_dir / name


def _skills_dir(context=None) -> Path | None:
    """Return the skills base directory for the current profile, or None when profileless."""
    profile = _active_profile(context)
    return profile.skills_dir if profile else None


class SkillTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name='skill',
            description=(
                'View and manage user skills. '
                'Use action="view" to load a skill\'s full content before applying it. '
                'Use create/edit/patch to maintain the skill library from background review. '
                'Skills live in ~/.operator/profiles/<profile>/skills/<name>/SKILL.md.'
            ),
            schema=SkillSchema,
            kind=ToolKind.Write,
            execution_mode=ToolExecutionMode.Sequential,
        )

    def get_display_name(self, args: dict) -> str:
        """Return a human-readable description of the action and skill name."""
        action = args.get('action', '')
        name = args.get('name', '') or ''
        if action == 'view': return f"Viewing skill: {name}" if name else "Viewing skill"
        if action == 'create': return f"Creating skill: {name}" if name else "Creating skill"
        if action == 'edit': return f"Editing skill: {name}" if name else "Editing skill"
        if action == 'patch': return f"Patching skill: {name}" if name else "Patching skill"
        if action == 'delete': return f"Deleting skill: {name}" if name else "Deleting skill"
        if action == 'write_file': return f"Writing skill file: {name}" if name else "Writing skill file"
        if action == 'remove_file': return f"Removing skill file: {name}" if name else "Removing skill file"
        return "Skill"

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        p = invocation.params
        action = p.get('action')
        name = (p.get('name') or '').strip()

        if action == 'view':
            return self._view(invocation.id, name, context)

        err = _validate_name(name)
        if err:
            return ToolResult.error(id=invocation.id, content=err)

        match action:
            case 'create':
                return self._create(invocation.id, name, p.get('content') or '', context)
            case 'edit':
                return self._edit(invocation.id, name, p.get('content') or '', context)
            case 'patch':
                return self._patch(
                    invocation.id, name,
                    p.get('file_path'), p.get('old_string') or '',
                    p.get('new_string') or '', bool(p.get('replace_all', False)),
                    context,
                )
            case 'delete':
                return self._delete(invocation.id, name, context)
            case 'write_file':
                return self._write_file(
                    invocation.id, name,
                    p.get('file_path') or '', p.get('file_content') or '',
                    context,
                )
            case 'remove_file':
                return self._remove_file(invocation.id, name, p.get('file_path') or '', context)
            case _:
                return ToolResult.error(id=invocation.id, content=f"Unknown action '{action}'.")

    def _view(self, inv_id: str, name: str, context: ToolContext | None) -> ToolResult:
        if not name:
            return ToolResult.error(id=inv_id, content="'name' is required.")
        loader = context.resource_loader if context else None
        if loader is None:
            return ToolResult.error(id=inv_id, content='Resource loader unavailable.')
        skills, _ = loader.get_skills()
        skill = next((s for s in skills if s.name == name), None)
        if skill is None:
            available = ', '.join(s.name for s in skills) or 'none'
            return ToolResult.error(
                id=inv_id,
                content=f"Skill '{name}' not found. Available: {available}",
            )
        try:
            content = skill.file_path.read_text(encoding='utf-8')
        except OSError as exc:
            return ToolResult.error(id=inv_id, content=f'Cannot read skill file: {exc}')
        skills_dir = _skills_dir(context)
        if skills_dir is not None:
            skill_usage.record_view(skills_dir, name)
        return ToolResult.ok(id=inv_id, content=content)

    def _create(self, inv_id: str, name: str, content: str, context: ToolContext | None) -> ToolResult:
        skill_dir = _skill_dir(name, context)
        if skill_dir is None:
            return ToolResult.error(id=inv_id, content=_NO_PROFILE_ERROR)
        if not content.strip():
            return ToolResult.error(id=inv_id, content="'content' is required for create.")
        if len(content) > MAX_SKILL_SIZE:
            return ToolResult.error(id=inv_id, content=f'Content exceeds {MAX_SKILL_SIZE} chars.')
        if (skill_dir / 'SKILL.md').exists():
            return ToolResult.error(id=inv_id, content=f"Skill '{name}' already exists. Use edit or patch.")
        skill_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(skill_dir / 'SKILL.md', content)
        skills_dir = _skills_dir(context)
        if skills_dir is not None:
            skill_usage.register(skills_dir, name)
        return ToolResult.ok(id=inv_id, content=f"Skill '{name}' created at {skill_dir}/SKILL.md")

    def _edit(self, inv_id: str, name: str, content: str, context: ToolContext | None) -> ToolResult:
        skill_dir = _skill_dir(name, context)
        if skill_dir is None:
            return ToolResult.error(id=inv_id, content=_NO_PROFILE_ERROR)
        if not content.strip():
            return ToolResult.error(id=inv_id, content="'content' is required for edit.")
        if len(content) > MAX_SKILL_SIZE:
            return ToolResult.error(id=inv_id, content=f'Content exceeds {MAX_SKILL_SIZE} chars.')
        skill_file = skill_dir / 'SKILL.md'
        if not skill_file.exists():
            return ToolResult.error(id=inv_id, content=f"Skill '{name}' not found. Use create first.")
        _atomic_write(skill_file, content)
        skills_dir = _skills_dir(context)
        if skills_dir is not None:
            skill_usage.record_patch(skills_dir, name)
        return ToolResult.ok(id=inv_id, content=f"Skill '{name}' updated.")

    def _patch(
        self, inv_id: str, name: str,
        file_path: str | None, old_string: str, new_string: str, replace_all: bool,
        context: ToolContext | None,
    ) -> ToolResult:
        skill_dir = _skill_dir(name, context)
        if skill_dir is None:
            return ToolResult.error(id=inv_id, content=_NO_PROFILE_ERROR)
        if not old_string:
            return ToolResult.error(id=inv_id, content="'old_string' is required for patch.")
        target = skill_dir / (file_path if file_path else 'SKILL.md')
        if not target.exists():
            return ToolResult.error(id=inv_id, content=f"File not found: {target}")
        text = target.read_text(encoding='utf-8')
        if old_string not in text:
            return ToolResult.error(id=inv_id, content=f"String not found in {target.name}.")
        updated = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
        if len(updated) > MAX_SKILL_SIZE:
            return ToolResult.error(id=inv_id, content=f'Patched content exceeds {MAX_SKILL_SIZE} chars.')
        _atomic_write(target, updated)
        skills_dir = _skills_dir(context)
        if skills_dir is not None:
            skill_usage.record_patch(skills_dir, name)
        return ToolResult.ok(id=inv_id, content=f"Patched {target.name} in skill '{name}'.")

    def _delete(self, inv_id: str, name: str, context: ToolContext | None) -> ToolResult:
        skills_dir = _skills_dir(context)
        if skills_dir is None:
            return ToolResult.error(id=inv_id, content=_NO_PROFILE_ERROR)
        skill_dir = _skill_dir(name, context)
        if skill_dir is None or not skill_dir.exists():
            return ToolResult.error(id=inv_id, content=f"Skill '{name}' not found.")
        if skill_usage.is_pinned(skills_dir, name):
            return ToolResult.error(id=inv_id, content=f"Skill '{name}' is pinned and cannot be deleted.")
        ok = archive_skill(skills_dir, name)
        if not ok:
            return ToolResult.error(id=inv_id, content=f"Failed to archive skill '{name}'.")
        return ToolResult.ok(id=inv_id, content=f"Skill '{name}' archived (recoverable via curator restore).")

    def _write_file(self, inv_id: str, name: str, file_path: str, file_content: str, context: ToolContext | None) -> ToolResult:
        skill_dir = _skill_dir(name, context)
        if skill_dir is None:
            return ToolResult.error(id=inv_id, content=_NO_PROFILE_ERROR)
        if not file_path:
            return ToolResult.error(id=inv_id, content="'file_path' is required for write_file.")
        if not any(file_path.startswith(p) for p in _SUPPORT_PREFIXES):
            return ToolResult.error(
                id=inv_id,
                content=f"file_path must start with one of: {', '.join(_SUPPORT_PREFIXES)}",
            )
        if not (skill_dir / 'SKILL.md').exists():
            return ToolResult.error(id=inv_id, content=f"Skill '{name}' not found.")
        target = skill_dir / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, file_content)
        return ToolResult.ok(id=inv_id, content=f"Written {file_path} under skill '{name}'.")

    def _remove_file(self, inv_id: str, name: str, file_path: str, context: ToolContext | None) -> ToolResult:
        skill_dir = _skill_dir(name, context)
        if skill_dir is None:
            return ToolResult.error(id=inv_id, content=_NO_PROFILE_ERROR)
        if not file_path:
            return ToolResult.error(id=inv_id, content="'file_path' is required for remove_file.")
        target = skill_dir / file_path
        if not target.exists():
            return ToolResult.error(id=inv_id, content=f"File not found: {file_path}")
        target.unlink()
        parent = target.parent
        while parent != skill_dir and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
        return ToolResult.ok(id=inv_id, content=f"Removed {file_path} from skill '{name}'.")


tool = SkillTool()

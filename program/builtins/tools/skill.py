"""skill — view, create, edit, patch, and delete user skills."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from program.settings.paths import get_skills_dir
from program.skill import usage as skill_usage
from program.skill.curator import archive_skill
from program.tool.types import Tool, ToolContext, ToolExecutionMode, ToolInvocation, ToolKind, ToolResult

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


def _validate_name(name: str) -> str | None:
    if not name or not _VALID_NAME_RE.match(name):
        return 'name must be lowercase letters, digits, hyphens, or underscores (2-64 chars)'
    return None


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    tmp.replace(path)


def _skill_dir(name: str) -> Path:
    return get_skills_dir() / name


class SkillTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name='skill',
            description=(
                'View and manage user skills. '
                'Use action="view" to load a skill\'s full content before applying it. '
                'Use create/edit/patch to maintain the skill library from background review. '
                'Skills live in ~/.program/agent/skills/<name>/SKILL.md.'
            ),
            schema=SkillSchema,
            kind=ToolKind.Write,
            execution_mode=ToolExecutionMode.Sequential,
        )

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
                return self._create(invocation.id, name, p.get('content') or '')
            case 'edit':
                return self._edit(invocation.id, name, p.get('content') or '')
            case 'patch':
                return self._patch(
                    invocation.id, name,
                    p.get('file_path'), p.get('old_string') or '',
                    p.get('new_string') or '', bool(p.get('replace_all', False)),
                )
            case 'delete':
                return self._delete(invocation.id, name)
            case 'write_file':
                return self._write_file(
                    invocation.id, name,
                    p.get('file_path') or '', p.get('file_content') or '',
                )
            case 'remove_file':
                return self._remove_file(invocation.id, name, p.get('file_path') or '')
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
        skill_usage.record_view(name)
        return ToolResult.ok(id=inv_id, content=content)

    def _create(self, inv_id: str, name: str, content: str) -> ToolResult:
        if not content.strip():
            return ToolResult.error(id=inv_id, content="'content' is required for create.")
        if len(content) > MAX_SKILL_SIZE:
            return ToolResult.error(id=inv_id, content=f'Content exceeds {MAX_SKILL_SIZE} chars.')
        skill_dir = _skill_dir(name)
        if (skill_dir / 'SKILL.md').exists():
            return ToolResult.error(id=inv_id, content=f"Skill '{name}' already exists. Use edit or patch.")
        skill_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(skill_dir / 'SKILL.md', content)
        skill_usage.register(name)
        return ToolResult.ok(id=inv_id, content=f"Skill '{name}' created at {skill_dir}/SKILL.md")

    def _edit(self, inv_id: str, name: str, content: str) -> ToolResult:
        if not content.strip():
            return ToolResult.error(id=inv_id, content="'content' is required for edit.")
        if len(content) > MAX_SKILL_SIZE:
            return ToolResult.error(id=inv_id, content=f'Content exceeds {MAX_SKILL_SIZE} chars.')
        skill_file = _skill_dir(name) / 'SKILL.md'
        if not skill_file.exists():
            return ToolResult.error(id=inv_id, content=f"Skill '{name}' not found. Use create first.")
        _atomic_write(skill_file, content)
        skill_usage.record_patch(name)
        return ToolResult.ok(id=inv_id, content=f"Skill '{name}' updated.")

    def _patch(
        self, inv_id: str, name: str,
        file_path: str | None, old_string: str, new_string: str, replace_all: bool,
    ) -> ToolResult:
        if not old_string:
            return ToolResult.error(id=inv_id, content="'old_string' is required for patch.")
        skill_dir = _skill_dir(name)
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
        skill_usage.record_patch(name)
        return ToolResult.ok(id=inv_id, content=f"Patched {target.name} in skill '{name}'.")

    def _delete(self, inv_id: str, name: str) -> ToolResult:
        skill_dir = _skill_dir(name)
        if not skill_dir.exists():
            return ToolResult.error(id=inv_id, content=f"Skill '{name}' not found.")
        if skill_usage.is_pinned(name):
            return ToolResult.error(id=inv_id, content=f"Skill '{name}' is pinned and cannot be deleted.")
        ok = archive_skill(name)
        if not ok:
            return ToolResult.error(id=inv_id, content=f"Failed to archive skill '{name}'.")
        return ToolResult.ok(id=inv_id, content=f"Skill '{name}' archived (recoverable via curator restore).")

    def _write_file(self, inv_id: str, name: str, file_path: str, file_content: str) -> ToolResult:
        if not file_path:
            return ToolResult.error(id=inv_id, content="'file_path' is required for write_file.")
        if not any(file_path.startswith(p) for p in _SUPPORT_PREFIXES):
            return ToolResult.error(
                id=inv_id,
                content=f"file_path must start with one of: {', '.join(_SUPPORT_PREFIXES)}",
            )
        skill_dir = _skill_dir(name)
        if not (skill_dir / 'SKILL.md').exists():
            return ToolResult.error(id=inv_id, content=f"Skill '{name}' not found.")
        target = skill_dir / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, file_content)
        return ToolResult.ok(id=inv_id, content=f"Written {file_path} under skill '{name}'.")

    def _remove_file(self, inv_id: str, name: str, file_path: str) -> ToolResult:
        if not file_path:
            return ToolResult.error(id=inv_id, content="'file_path' is required for remove_file.")
        target = _skill_dir(name) / file_path
        if not target.exists():
            return ToolResult.error(id=inv_id, content=f"File not found: {file_path}")
        target.unlink()
        skill_dir = _skill_dir(name)
        parent = target.parent
        while parent != skill_dir and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
        return ToolResult.ok(id=inv_id, content=f"Removed {file_path} from skill '{name}'.")


tool = SkillTool()

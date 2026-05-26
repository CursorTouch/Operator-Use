from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from program.resource.types import ContextFile
    from program.skill.types import Skill


def build_guidelines(extra: list[str]) -> str:
    lines = [f"- {g.strip()}" for g in extra if g.strip()]
    return "\n".join(lines)


def context_files_section(context_files: list[ContextFile]) -> str:
    if not context_files:
        return ""
    parts = ["\n\n# Project Context\n\nProject-specific instructions and guidelines:\n"]
    for cf in context_files:
        parts.append(f"## {cf.path}\n\n{cf.content}\n")
    return "\n".join(parts)


def escape_xml(text: str) -> str:
    return (
        text.replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&apos;')
    )


def format_skills_for_prompt(skills: list[Skill], available_tools: set[str] | None = None) -> str:
    visible = [
        s for s in skills
        if not s.disable_model_invocation
        and (
            not s.requires_tools
            or available_tools is None
            or all(t in available_tools for t in s.requires_tools)
        )
    ]
    if not visible:
        return ''

    lines = [
        '',
        '',
        'The following skills provide specialized instructions for specific tasks.',
        'Before replying to any task, scan the skills below. If a skill matches or is even '
        'partially relevant, you MUST load it with skill_view before proceeding.',
        'When a skill file references a relative path, resolve it against the skill directory '
        '(parent of SKILL.md) and use that absolute path in tool commands.',
        '',
        '<available_skills>',
    ]

    for skill in visible:
        lines.append('  <skill>')
        lines.append(f'    <name>{escape_xml(skill.name)}</name>')
        lines.append(f'    <description>{escape_xml(skill.description)}</description>')
        lines.append('  </skill>')

    lines.append('</available_skills>')
    return '\n'.join(lines)

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from program.prompt.types import SystemPromptOptions

if TYPE_CHECKING:
    from program.skill.types import Skill
    from program.tool.types import Tool

_BASE_GUIDELINES = [
    "Be concise in your responses",
    "Show file paths clearly when working with files",
]


def _build_guidelines(tools: list[Tool], extra: list[str]) -> str:
    names = {t.name for t in tools}
    seen: set[str] = set()
    lines: list[str] = []

    def add(g: str) -> None:
        if g not in seen:
            seen.add(g)
            lines.append(f"- {g}")

    has_bash = bool(names & {"bash", "terminal"})
    has_file_tools = bool(names & {"grep", "glob", "ls"})

    if has_bash and not has_file_tools:
        add("Use bash for file operations like ls, grep, find")
    elif has_bash and has_file_tools:
        add("Prefer grep/glob/ls tools over bash for file exploration (faster, respects .gitignore)")

    for guideline in extra:
        normalized = guideline.strip()
        if normalized:
            add(normalized)

    for g in _BASE_GUIDELINES:
        add(g)

    return "\n".join(lines)


def _build_tools_list(tools: list[Tool]) -> str:
    if not tools:
        return "(none)"
    return "\n".join(f"- {t.name}: {t.description}" for t in tools)


def _context_files_section(context_files: list) -> str:
    if not context_files:
        return ""
    parts = ["\n\n# Project Context\n\nProject-specific instructions and guidelines:\n"]
    for cf in context_files:
        parts.append(f"## {cf.path}\n\n{cf.content}\n")
    return "\n".join(parts)


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
        "Use the read tool to load a skill's file when the task matches its description.",
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


def build_system_prompt(options: SystemPromptOptions) -> str:
    today = date.today().isoformat()
    cwd = options.cwd.replace("\\", "/")
    footer = f"\nCurrent date: {today}\nCurrent working directory: {cwd}"

    has_read = any(t.name == "read" for t in options.tools)

    append_section = f"\n\n{options.append_system_prompt}" if options.append_system_prompt else ""
    context_section = _context_files_section(options.context_files)
    skills_section = format_skills_for_prompt(options.skills) if has_read and options.skills else ""

    if options.custom_prompt:
        return options.custom_prompt + append_section + context_section + skills_section + footer

    tools_list = _build_tools_list(options.tools)
    guidelines = _build_guidelines(options.tools, options.prompt_guidelines)

    prompt = f"""You are an expert coding assistant operating inside a coding agent harness. \
You help users by reading files, executing commands, editing code, and writing new files.

Available tools:
{tools_list}

In addition to the tools above, you may have access to other custom tools depending on the project.

Guidelines:
{guidelines}"""

    return prompt + append_section + context_section + skills_section + footer

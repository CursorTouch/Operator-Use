from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from program.prompts.types import BuildSystemPromptOptions
from program.skill.loader import format_skills_for_prompt

if TYPE_CHECKING:
    from program.skill.types import Skill

_DEFAULT_TOOLS = ["read", "bash", "edit", "write"]

_TOOL_GUIDELINES: dict[str, list[str]] = {
    "grep": ["Prefer grep over bash for text search — faster and respects .gitignore"],
    "glob": ["Prefer glob over bash for file pattern matching"],
    "ls":   ["Prefer ls tool over bash for directory listing"],
}

_BASE_GUIDELINES = [
    "Be concise in your responses",
    "Show file paths clearly when working with files",
]


def _build_guidelines(tools: list[str], extra: list[str]) -> str:
    seen: set[str] = set()
    lines: list[str] = []

    def add(g: str) -> None:
        if g not in seen:
            seen.add(g)
            lines.append(f"- {g}")

    has_bash = "bash" in tools or "terminal" in tools
    has_grep = "grep" in tools
    has_glob = "glob" in tools
    has_ls = "ls" in tools

    if has_bash and not has_grep and not has_glob and not has_ls:
        add("Use bash for file operations like ls, grep, find")
    elif has_bash and (has_grep or has_glob or has_ls):
        add("Prefer grep/glob/ls tools over bash for file exploration (faster, respects .gitignore)")

    for guideline in extra:
        normalized = guideline.strip()
        if normalized:
            add(normalized)

    for g in _BASE_GUIDELINES:
        add(g)

    return "\n".join(lines)


def _build_tools_list(tools: list[str], snippets: dict[str, str]) -> str:
    visible = [t for t in tools if t in snippets]
    if not visible:
        return "(none)"
    return "\n".join(f"- {t}: {snippets[t]}" for t in visible)


def _context_files_section(context_files: list) -> str:
    if not context_files:
        return ""
    parts = ["\n\n# Project Context\n\nProject-specific instructions and guidelines:\n"]
    for cf in context_files:
        parts.append(f"## {cf.path}\n\n{cf.content}\n")
    return "\n".join(parts)


def build_system_prompt(options: BuildSystemPromptOptions) -> str:
    today = date.today().isoformat()
    cwd = options.cwd.replace("\\", "/")

    tools = options.selected_tools if options.selected_tools is not None else _DEFAULT_TOOLS
    has_read = "read" in tools

    append_section = f"\n\n{options.append_system_prompt}" if options.append_system_prompt else ""
    context_section = _context_files_section(options.context_files)
    skills_section = format_skills_for_prompt(options.skills) if has_read and options.skills else ""  # type: ignore[arg-type]

    footer = f"\nCurrent date: {today}\nCurrent working directory: {cwd}"

    if options.custom_prompt:
        prompt = options.custom_prompt
        prompt += append_section
        prompt += context_section
        prompt += skills_section
        prompt += footer
        return prompt

    tools_list = _build_tools_list(tools, options.tool_snippets)
    guidelines = _build_guidelines(tools, options.prompt_guidelines)

    prompt = f"""You are an expert coding assistant operating inside a coding agent harness. \
You help users by reading files, executing commands, editing code, and writing new files.

Available tools:
{tools_list}

In addition to the tools above, you may have access to other custom tools depending on the project.

Guidelines:
{guidelines}"""

    prompt += append_section
    prompt += context_section
    prompt += skills_section
    prompt += footer

    return prompt

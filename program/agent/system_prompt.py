from __future__ import annotations

from datetime import date
from typing import Optional

from program.skill.format import format_skills_for_prompt
from program.skill.types import Skill


def build_system_prompt(
    cwd: str,
    *,
    custom_prompt: Optional[str] = None,
    selected_tools: Optional[list[str]] = None,
    tool_snippets: Optional[dict[str, str]] = None,
    prompt_guidelines: Optional[list[str]] = None,
    append_system_prompt: Optional[str] = None,
    context_files: Optional[list[dict]] = None,
    skills: Optional[list[Skill]] = None,
) -> str:
    today = date.today().isoformat()
    prompt_cwd = cwd.replace("\\", "/")
    append_section = f"\n\n{append_system_prompt}" if append_system_prompt else ""
    ctx_files = context_files or []
    skill_list = skills or []

    if custom_prompt:
        prompt = custom_prompt
        if append_section:
            prompt += append_section
        if ctx_files:
            prompt += _render_context_files(ctx_files)
        has_read = not selected_tools or "read" in selected_tools
        if has_read and skill_list:
            prompt += format_skills_for_prompt(skill_list)
        prompt += f"\nCurrent date: {today}"
        prompt += f"\nCurrent working directory: {prompt_cwd}"
        return prompt

    # -------------------------------------------------------------------------
    # Default coding-agent prompt
    # -------------------------------------------------------------------------
    tools = selected_tools or ["read", "bash", "edit", "write"]
    snippets = tool_snippets or {}

    visible_tools = [t for t in tools if t in snippets]
    tools_list = (
        "\n".join(f"- {t}: {snippets[t]}" for t in visible_tools)
        if visible_tools
        else "(none)"
    )

    guidelines: list[str] = []
    seen: set[str] = set()

    def add(g: str) -> None:
        if g not in seen:
            seen.add(g)
            guidelines.append(g)

    has_bash = "bash" in tools
    has_grep = "grep" in tools
    has_find = "find" in tools
    has_ls = "ls" in tools
    has_read = "read" in tools

    if has_bash and not has_grep and not has_find and not has_ls:
        add("Use bash for file operations like ls, rg, find")
    elif has_bash and (has_grep or has_find or has_ls):
        add("Prefer grep/find/ls tools over bash for file exploration (faster, respects .gitignore)")

    for g in prompt_guidelines or []:
        g = g.strip()
        if g:
            add(g)

    add("Be concise in your responses")
    add("Show file paths clearly when working with files")

    guidelines_text = "\n".join(f"- {g}" for g in guidelines)

    prompt = f"""\
You are an expert coding assistant operating inside pi, a coding agent harness. \
You help users by reading files, executing commands, editing code, and writing new files.

Available tools:
{tools_list}

In addition to the tools above, you may have access to other custom tools depending on the project.

Guidelines:
{guidelines_text}\
"""

    if append_section:
        prompt += append_section

    if ctx_files:
        prompt += _render_context_files(ctx_files)

    if has_read and skill_list:
        prompt += format_skills_for_prompt(skill_list)

    prompt += f"\nCurrent date: {today}"
    prompt += f"\nCurrent working directory: {prompt_cwd}"
    return prompt


def _render_context_files(context_files: list[dict]) -> str:
    out = "\n\n# Project Context\n\nProject-specific instructions and guidelines:\n\n"
    for f in context_files:
        out += f"## {f['path']}\n\n{f['content']}\n\n"
    return out

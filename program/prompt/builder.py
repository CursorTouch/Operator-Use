from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from program.prompt.types import ContextFile, SystemPromptOptions
from program.prompt.utils import build_guidelines, context_files_section, format_skills_for_prompt

if TYPE_CHECKING:
    from program.skill.types import Skill
    from program.tool.types import Tool


class PromptTemplate:
    def __init__(
        self,
        cwd: str,
        custom_prompt: str | None = None,
        tools: list[Tool] | None = None,
        prompt_guidelines: list[str] | None = None,
        append_system_prompt: str | None = None,
        context_files: list[ContextFile] | None = None,
        skills: list[Skill] | None = None,
    ) -> None:
        self.cwd = cwd
        self.custom_prompt = custom_prompt
        self.tools: list[Tool] = tools or []
        self.prompt_guidelines: list[str] = prompt_guidelines or []
        self.append_system_prompt = append_system_prompt
        self.context_files: list[ContextFile] = context_files or []
        self.skills: list[Skill] = skills or []

    def build(self) -> str:
        today = date.today().isoformat()
        cwd = self.cwd.replace("\\", "/")
        global_temp = Path.home() / ".program" / "temp"
        project_temp = Path(self.cwd) / ".program" / "temp"
        footer = (
            f"\nCurrent date: {today}\nCurrent working directory: {cwd}"
            f"\nGlobal temp directory: {global_temp} (scratch space shared across projects)"
            f"\nProject temp directory: {project_temp} (scratch space for this project)"
        )

        has_read = any(t.name == "read" for t in self.tools)

        append_section = f"\n\n{self.append_system_prompt}" if self.append_system_prompt else ""
        context_section = context_files_section(self.context_files)
        skills_section = format_skills_for_prompt(self.skills) if has_read and self.skills else ""

        if self.custom_prompt:
            return self.custom_prompt + append_section + context_section + skills_section + footer

        guidelines = build_guidelines(self.prompt_guidelines)
        prompt = "You are a helpful assistant."
        if guidelines:
            prompt += f"\n\nGuidelines:\n{guidelines}"

        return prompt + append_section + context_section + skills_section + footer


def build_system_prompt(options: SystemPromptOptions) -> str:
    return PromptTemplate(
        cwd=options.cwd,
        custom_prompt=options.custom_prompt,
        tools=options.tools,
        prompt_guidelines=options.prompt_guidelines,
        append_system_prompt=options.append_system_prompt,
        context_files=options.context_files,
        skills=options.skills,
    ).build()

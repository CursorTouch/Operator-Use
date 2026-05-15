from __future__ import annotations

from pydantic import BaseModel, Field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from program.skill.types import Skill


class ContextFile(BaseModel):
    path: str
    content: str


class BuildSystemPromptOptions(BaseModel):
    cwd: str
    custom_prompt: str | None = None
    selected_tools: list[str] | None = None
    tool_snippets: dict[str, str] = Field(default_factory=dict)
    prompt_guidelines: list[str] = Field(default_factory=list)
    append_system_prompt: str | None = None
    context_files: list[ContextFile] = Field(default_factory=list)
    skills: list[object] = Field(default_factory=list)  # list[Skill]

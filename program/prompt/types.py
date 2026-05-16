from __future__ import annotations

from pydantic import BaseModel, Field

from program.skill.types import Skill
from program.tool.types import Tool


class ContextFile(BaseModel):
    path: str
    content: str


class SystemPromptOptions(BaseModel):
    model_config = {'arbitrary_types_allowed': True}

    cwd: str
    custom_prompt: str | None = None
    tools: list[Tool] = Field(default_factory=list)
    prompt_guidelines: list[str] = Field(default_factory=list)
    append_system_prompt: str | None = None
    context_files: list[ContextFile] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)

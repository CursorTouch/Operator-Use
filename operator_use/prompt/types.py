from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from operator_use.skill.types import Skill
from operator_use.tool.types import Tool


class SystemPromptOptions(BaseModel):
    model_config = {'arbitrary_types_allowed': True}

    cwd: str
    custom_prompt: str | None = None
    operation_manual: str | None = None
    tools: list[Tool] = Field(default_factory=list)
    prompt_guidelines: list[str] = Field(default_factory=list)
    append_system_prompt: str | None = None
    skills: list[Skill] = Field(default_factory=list)
    soul_prompt: str | None = None
    user_profile: str | None = None
    agent_memory: str | None = None
    channel: str | None = None
    profile_dir: Path | None = None

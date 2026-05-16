from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from program.tool.types import Tool


class RuntimeConfig(BaseModel):
    """Public config passed to RuntimeLoader.create()."""
    model_config = {'arbitrary_types_allowed': True}

    cwd: Path
    config_dir: Path | None = None

    # LLM
    model_id: str = 'claude-sonnet-4-6'
    provider: str | None = None

    # Session
    session_file: Path | None = None
    persist_session: bool = True

    # Tools & prompt
    tools: list[Tool] = Field(default_factory=list)
    selected_tools: list[str] | None = None
    tool_snippets: dict[str, str] = Field(default_factory=dict)
    prompt_guidelines: list[str] = Field(default_factory=list)

    # Resource loader
    no_extensions: bool = False
    no_skills: bool = False
    no_context_files: bool = False
    system_prompt: str | None = None
    append_system_prompt: list[str] = Field(default_factory=list)

    # Compaction
    compaction_enabled: bool = True
    compaction_reserve_tokens: int = 16384
    compaction_keep_recent_tokens: int = 20000

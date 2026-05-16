from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from program.tool.types import Tool


class SessionConfig(BaseModel):
    """Internal runtime config passed to AgentSession.__init__."""
    model_config = {'arbitrary_types_allowed': True}

    cwd: Path
    model: Any | None = None                    # Model | None
    context_window: int = 200_000
    selected_tools: list[str] | None = None
    tool_snippets: dict[str, str] = {}
    prompt_guidelines: list[str] = []
    retry_enabled: bool = True
    retry_max_retries: int = 3
    retry_base_delay_ms: int = 2000


class AgentSessionConfig(BaseModel):
    """Public config passed to AgentSessionLoader.create()."""
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
    tools: list['Tool'] = Field(default_factory=list)
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


class PromptOptions(BaseModel):
    source: Literal['interactive', 'rpc', 'extension'] = 'interactive'
    compaction_custom_instructions: str | None = None


@dataclass
class CompactionStartEvent:
    type: Literal['compaction_start'] = field(default='compaction_start', init=False)
    tokens_before: int = 0


@dataclass
class CompactionEndEvent:
    type: Literal['compaction_end'] = field(default='compaction_end', init=False)
    tokens_before: int = 0
    summary: str = ''


@dataclass
class RetryStartEvent:
    type: Literal['retry_start'] = field(default='retry_start', init=False)
    attempt: int = 0
    max_retries: int = 0


@dataclass
class RetryEndEvent:
    type: Literal['retry_end'] = field(default='retry_end', init=False)
    attempt: int = 0
    success: bool = True
    error: str | None = None

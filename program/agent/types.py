from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel
from program.message.types import BaseMessage

if TYPE_CHECKING:
    from program.tool.types import Tool


@dataclass
class AgentContext:
    """Snapshot of everything the LLM receives for one turn."""
    system_prompt: str
    messages: list[BaseMessage]
    tools: list[Tool] = field(default_factory=list)


class AgentConfig(BaseModel):
    """Internal runtime config passed to Agent.__init__."""
    model_config = {'arbitrary_types_allowed': True}

    cwd: Path
    model: Any | None = None
    context_window: int = 200_000
    prompt_guidelines: list[str] = []
    retry_enabled: bool = True
    retry_max_retries: int = 3
    retry_base_delay_ms: int = 2000


class PromptOptions(BaseModel):
    source: Literal['interactive', 'rpc', 'extension', 'cron'] = 'interactive'
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

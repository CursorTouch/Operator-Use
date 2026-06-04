from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel
from operator_use.message.types import LLMMessage
from operator_use.session.types import MessageMeta

if TYPE_CHECKING:
    from operator_use.tool.types import Tool


class AgentPhase(str, Enum):
    """Agent execution phase."""
    IDLE = "idle"
    TURN = "turn"
    COMPACTION = "compaction"


@dataclass
class AgentContext:
    """Snapshot of everything the LLM receives for one turn."""
    system_prompt: str
    messages: list[LLMMessage]
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
    """Per-turn invocation options passed to Agent.invoke()."""
    source: Literal['interactive', 'rpc', 'extension', 'cron', 'subagent', 'goal', 'queue', 'background'] = 'interactive'
    compaction_custom_instructions: str | None = None
    meta: MessageMeta | None = None
    channel: str | None = None
    images: list[str] = []  # local file paths to attach as ImageContent


@dataclass
class CompactionStartEvent:
    """Emitted before the compaction strategy runs; carries pre-compaction token count."""
    type: Literal['compaction_start'] = field(default='compaction_start', init=False)
    tokens_before: int = 0


@dataclass
class CompactionEndEvent:
    """Emitted after compaction completes with the resulting summary and original token count."""
    type: Literal['compaction_end'] = field(default='compaction_end', init=False)
    tokens_before: int = 0
    summary: str = ''


@dataclass
class RetryStartEvent:
    """Emitted before each retry attempt after a transient engine failure."""
    type: Literal['retry_start'] = field(default='retry_start', init=False)
    attempt: int = 0
    max_retries: int = 0


@dataclass
class RetryEndEvent:
    """Emitted after a retry attempt resolves (success=True) or fails (success=False, error set)."""
    type: Literal['retry_end'] = field(default='retry_end', init=False)
    attempt: int = 0
    success: bool = True
    error: str | None = None

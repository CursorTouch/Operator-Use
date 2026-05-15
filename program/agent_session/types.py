from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel


class AgentSessionConfig(BaseModel):
    model_config = {'arbitrary_types_allowed': True}

    cwd: Path
    model: Any | None = None                    # Model | None
    selected_tools: list[str] | None = None
    tool_snippets: dict[str, str] = {}
    prompt_guidelines: list[str] = []


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

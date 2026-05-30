from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class SubagentStatus(StrEnum):
    running   = 'running'
    completed = 'completed'
    failed    = 'failed'
    cancelled = 'cancelled'


# How a finished subagent's result is delivered back to its origin:
#   agent   — re-enter the main agent's LLM loop (it summarizes / reacts)
#   channel — raw result sent straight to the channel, no LLM turn
DeliveryMode = Literal['agent', 'channel']


@dataclass
class SubagentRecord:
    task_id: str
    label: str
    task: str
    status: SubagentStatus
    started_at: datetime
    finished_at: datetime | None = None
    result: str | None = None
    channel: str | None = None   # originating channel name (e.g. 'telegram', 'discord')
    chat_id: str | None = None   # originating chat/conversation ID within that channel
    depends_on: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 0
    profile: str | None = None           # named profile used for this subagent
    system_prompt: str | None = None     # profile's system prompt (overrides default)
    tool_names: list[str] | None = None  # profile's allowed tools (None = all)
    spawn_depth: int = 0                 # nesting depth (0 = spawned by main agent)
    fork: bool = False                   # inherits parent conversation context
    parent_messages: list | None = None  # effective parent history (fork only)
    parent_system_prompt: str | None = None  # parent system prompt (fork only)
    team_id: str | None = None           # team this subagent belongs to (if any)
    deliver: DeliveryMode = 'agent'      # how _announce routes the result


class SubagentSettings(BaseModel):
    enabled: bool = True
    max_concurrent: int = 10
    max_iterations: int = 20
    max_spawn_depth: int = 3
    timeout: float = 300.0
    system_prompt: str | None = None
    max_retries: int = 0
    retry_base_delay: float = 1.0
    retry_backoff_factor: float = 2.0
    retry_max_delay: float = 30.0

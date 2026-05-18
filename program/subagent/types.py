from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel


@dataclass
class SubagentRecord:
    task_id: str
    label: str
    task: str
    status: str          # 'running' | 'completed' | 'failed' | 'cancelled'
    started_at: datetime
    finished_at: datetime | None = None
    result: str | None = None
    channel: str | None = None   # originating channel name (e.g. 'telegram', 'discord')
    chat_id: str | None = None   # originating chat/conversation ID within that channel
    depends_on: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 0


class SubagentSettings(BaseModel):
    max_concurrent: int = 10
    max_iterations: int = 20
    timeout: float = 300.0
    system_prompt: str | None = None
    max_retries: int = 0
    retry_base_delay: float = 1.0
    retry_backoff_factor: float = 2.0
    retry_max_delay: float = 30.0

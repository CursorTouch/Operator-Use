from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class WorkflowStatus(StrEnum):
    running   = 'running'
    completed = 'completed'
    failed    = 'failed'
    cancelled = 'cancelled'


@dataclass
class WorkflowMeta:
    name: str
    description: str
    when_to_use: str | None = None
    phases: list[dict] = field(default_factory=list)


@dataclass
class WorkflowRunRecord:
    run_id: str
    workflow_name: str
    status: WorkflowStatus
    started_at: datetime
    finished_at: datetime | None = None
    result: Any = None
    error: str | None = None
    log_lines: list[str] = field(default_factory=list)
    agent_calls: int = 0
    current_phase: str | None = None
    channel: str | None = None
    chat_id: str | None = None

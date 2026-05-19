from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProcessStatus(str, Enum):
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    KILLED = 'killed'


@dataclass
class ProcessRecord:
    id: str                         # p{uuid8}  e.g. "p3f7e2c1"
    command: str                    # shell command
    description: str                # user-visible label
    status: ProcessStatus
    cwd: str                        # working directory when spawned
    created_at: float               # unix timestamp
    started_at: float | None = None
    ended_at: float | None = None
    return_code: int | None = None
    env: dict[str, str] | None = None

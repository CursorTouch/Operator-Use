from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal


ProcessType = Literal['shell', 'agent']


class ProcessStatus(str, Enum):
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    KILLED = 'killed'


@dataclass
class ProcessRecord:
    id: str                             # p{uuid8} / a{uuid8}
    command: str | None                 # shell command (shell tasks) or None (agent tasks)
    description: str                    # user-visible label
    status: ProcessStatus
    cwd: str                            # working directory when spawned
    created_at: float                   # unix timestamp
    type: ProcessType = 'shell'
    prompt: str | None = None           # initial prompt (agent tasks only)
    output_file: Path | None = None     # disk log path (agent tasks only)
    started_at: float | None = None
    ended_at: float | None = None
    return_code: int | None = None
    env: dict[str, str] | None = None
    metadata: dict[str, str] = field(default_factory=dict)

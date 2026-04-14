from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SubagentRecord:
    task_id: str
    label: str
    task: str
    channel: str
    chat_id: str
    account_id: str
    status: str  # "running" | "completed" | "failed" | "cancelled"
    started_at: datetime
    finished_at: datetime | None = None
    result: str | None = None
    depends_on: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 0

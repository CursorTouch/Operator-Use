from dataclasses import dataclass, field
from typing import Literal


@dataclass
class CronSchedule:
    mode: Literal["at", "every", "cron"]
    interval_ms: int | None = None
    expr: str | None = None
    tz: str | None = None


@dataclass
class CronPayload:
    message: str = ""
    deliver: bool = False
    channel: str | None = None
    chat_id: str | None = None
    account_id: str = ""


@dataclass
class CronJobState:
    next_run_at_ms: int | None = None
    last_run_at_ms: int | None = None
    last_status: Literal["success", "failure", "skipped"] | None = None
    last_error: str | None = None


@dataclass
class CronJob:
    id: str
    name: str
    enabled: bool = True
    schedule: CronSchedule = field(
        default_factory=lambda: CronSchedule(mode="cron", expr="* * * * *", tz="UTC")
    )
    payload: CronPayload = field(default_factory=CronPayload)
    state: CronJobState = field(default_factory=CronJobState)
    created_at_ms: int = 0
    updated_at_ms: int = 0
    delete_after_run: bool = False


@dataclass
class CronStore:
    version: int = 1
    jobs: list[CronJob] = field(default_factory=list)

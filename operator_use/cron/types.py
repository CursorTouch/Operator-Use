from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class CronSchedule:
    mode: Literal['every', 'cron']
    interval_ms: int | None = None   # used when mode='every'
    expr: str | None = None          # cron expression, used when mode='cron'
    tz: str = 'UTC'                  # IANA timezone, used when mode='cron'


@dataclass
class CronPayload:
    message: str = ''                # prompt injected into the agent
    channel_id: str | None = None    # route response to this channel (e.g. 'telegram')
    chat_id: str | None = None       # specific chat/user within the channel
    deliver: bool = False            # True = send result directly to channel; False = route through agent
    profile: str | None = None       # set → run the message on a subagent with this profile


@dataclass
class CronJobState:
    next_run_at_ms: int | None = None
    last_run_at_ms: int | None = None
    last_status: Literal['success', 'failure'] | None = None
    last_error: str | None = None


@dataclass
class CronJob:
    id: str
    name: str
    enabled: bool
    schedule: CronSchedule
    payload: CronPayload
    state: CronJobState = field(default_factory=CronJobState)
    created_at_ms: int = 0
    updated_at_ms: int = 0
    delete_after_run: bool = False


@dataclass
class CronStore:
    version: int = 1
    jobs: list[CronJob] = field(default_factory=list)

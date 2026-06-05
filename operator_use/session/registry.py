"""SessionRegistry — tracks ACP and subagent sessions spawned from a main agent.

The registry is created per-Agent and written to by the acp_agent and subagent tools
when they spawn sessions. The Agent reads it at invoke() time to inject recent session
context into the context-only user message prefix alongside memory.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


SessionKind = Literal['subagent', 'acp']


@dataclass
class ContextFrame:
    """One entry in the agent's context stack — describes which session context is active."""

    kind: Literal['main', 'registry']
    label: str
    registry_key: str | None = None   # set when kind == 'registry'


@dataclass
class SessionRecord:
    """One spawned sub-session or subagent task, tracked for context injection."""

    key: str                        # task_id (subagent) or session_id (ACP)
    kind: SessionKind            # 'subagent' | 'acp'
    agent: str                      # profile name (subagent) or ACP agent name
    label: str                      # short human label
    task: str                       # what was delegated
    status: str                     # 'running' | 'done' | 'failed' | 'cancelled'
    spawned_at: datetime
    result: str | None = None
    finished_at: datetime | None = None
    # ACP-only: stored so sessions_send can reconnect without the full registry
    _acp_config: Any = field(default=None, repr=False, compare=False)


class SessionRegistry:
    """Tracks sub-sessions and subagent tasks spawned by the main agent."""

    def __init__(self) -> None:
        self._records: dict[str, SessionRecord] = {}

    def register(self, record: SessionRecord) -> None:
        self._records[record.key] = record

    def update(
        self,
        key: str,
        *,
        status: str | None = None,
        result: str | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        r = self._records.get(key)
        if r is None:
            return
        if status is not None:
            r.status = status
        if result is not None:
            r.result = result
        if finished_at is not None:
            r.finished_at = finished_at

    def get(self, key: str) -> SessionRecord | None:
        return self._records.get(key)

    def list_recent(self, limit: int = 8) -> list[SessionRecord]:
        return sorted(self._records.values(), key=lambda r: r.spawned_at, reverse=True)[:limit]

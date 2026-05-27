from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

TeamMemberStatus = Literal["active", "idle", "stopped"]
TeamStatus = Literal["active", "dissolved"]


@dataclass
class TeamMember:
    agent_id: str         # subagent task_id
    name: str             # human-readable name
    role: str             # profile name used to spawn this member
    joined_at: float      # epoch seconds
    status: TeamMemberStatus = "active"
    model: str | None = None


@dataclass
class TeamRecord:
    name: str
    description: str
    created_at: float     # epoch seconds
    creator_id: str       # "root" for main agent, task_id for subagent coordinators
    members: list[TeamMember] = field(default_factory=list)
    status: TeamStatus = "active"


@dataclass
class MailboxMessage:
    id: str
    type: str             # e.g. "message", "result", "note"
    sender_id: str
    content: str
    created_at: float     # epoch seconds

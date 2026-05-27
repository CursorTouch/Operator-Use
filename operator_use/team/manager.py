"""TeamManager — persists team state across sessions.

Teams are stored as JSON files at:
  ~/.operator/agent/teams/<team_name>/team.json

Each member can have an inbox at:
  ~/.operator/agent/teams/<team_name>/agents/<agent_id>/inbox/
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from operator_use.team.mailbox import TeamMailbox
from operator_use.team.types import MailboxMessage, TeamMember, TeamMemberStatus, TeamRecord

logger = logging.getLogger(__name__)


_NO_PROFILE_ERROR = "Team operations require an active profile. Start the agent with --profile."


class TeamManager:
    def __init__(self, base_path: Path | None) -> None:
        self._base = base_path
        self._teams: dict[str, TeamRecord] = {}
        if base_path is not None:
            base_path.mkdir(parents=True, exist_ok=True)
            self._load_all()

    # ── Team CRUD ─────────────────────────────────────────────────────────────

    def create(self, name: str, description: str, creator_id: str = "root") -> TeamRecord:
        if self._base is None:
            raise RuntimeError(_NO_PROFILE_ERROR)
        if name in self._teams:
            raise ValueError(f"Team '{name}' already exists.")
        record = TeamRecord(
            name=name,
            description=description,
            created_at=time.time(),
            creator_id=creator_id,
        )
        self._teams[name] = record
        self._save(record)
        return record

    def dissolve(self, name: str) -> None:
        record = self._get_or_raise(name)
        record.status = "dissolved"
        self._save(record)

    def get(self, name: str) -> TeamRecord | None:
        return self._teams.get(name)

    def list_teams(self) -> list[TeamRecord]:
        return list(self._teams.values())

    # ── Member management ─────────────────────────────────────────────────────

    def add_member(self, team_name: str, member: TeamMember) -> None:
        record = self._get_or_raise(team_name)
        record.members.append(member)
        self._save(record)

    def update_member_status(
        self, team_name: str, agent_id: str, status: TeamMemberStatus
    ) -> None:
        record = self._teams.get(team_name)
        if record is None:
            return
        for m in record.members:
            if m.agent_id == agent_id:
                m.status = status
                self._save(record)
                return

    def get_member(self, team_name: str, agent_id: str) -> TeamMember | None:
        record = self._teams.get(team_name)
        if record is None:
            return None
        return next((m for m in record.members if m.agent_id == agent_id), None)

    def find_member_by_name(self, team_name: str, name: str) -> TeamMember | None:
        record = self._teams.get(team_name)
        if record is None:
            return None
        return next((m for m in record.members if m.name.lower() == name.lower()), None)

    # ── Mailbox ───────────────────────────────────────────────────────────────

    def mailbox(self, team_name: str, agent_id: str) -> TeamMailbox:
        return TeamMailbox(self._base, team_name, agent_id)

    async def send_message(
        self,
        team_name: str,
        to_agent_id: str,
        sender_id: str,
        type: str,
        content: str,
    ) -> str:
        return await self.mailbox(team_name, to_agent_id).send(type, sender_id, content)

    async def read_inbox(self, team_name: str, agent_id: str) -> list[MailboxMessage]:
        return await self.mailbox(team_name, agent_id).receive()

    async def peek_inbox(self, team_name: str, agent_id: str) -> list[MailboxMessage]:
        return await self.mailbox(team_name, agent_id).peek()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save(self, record: TeamRecord) -> None:
        if self._base is None:
            return
        team_dir = self._base / record.name
        team_dir.mkdir(parents=True, exist_ok=True)
        path = team_dir / "team.json"
        tmp = team_dir / ".team.json.tmp"
        data = {
            "name": record.name,
            "description": record.description,
            "created_at": record.created_at,
            "creator_id": record.creator_id,
            "status": record.status,
            "members": [
                {
                    "agent_id": m.agent_id,
                    "name": m.name,
                    "role": m.role,
                    "joined_at": m.joined_at,
                    "status": m.status,
                    "model": m.model,
                }
                for m in record.members
            ],
        }
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.rename(tmp, path)

    def _load_all(self) -> None:
        if self._base is None:
            return
        for team_json in self._base.glob("*/team.json"):
            try:
                data = json.loads(team_json.read_text(encoding="utf-8"))
                members = [
                    TeamMember(
                        agent_id=m["agent_id"],
                        name=m["name"],
                        role=m["role"],
                        joined_at=m["joined_at"],
                        status=m.get("status", "stopped"),
                        model=m.get("model"),
                    )
                    for m in data.get("members", [])
                ]
                record = TeamRecord(
                    name=data["name"],
                    description=data["description"],
                    created_at=data["created_at"],
                    creator_id=data.get("creator_id", "root"),
                    members=members,
                    status=data.get("status", "active"),
                )
                self._teams[record.name] = record
            except Exception:
                logger.warning("Failed to load team from %s", team_json, exc_info=True)

    def _get_or_raise(self, name: str) -> TeamRecord:
        record = self._teams.get(name)
        if record is None:
            raise ValueError(f"No team named '{name}'.")
        return record

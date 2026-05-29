"""File-based async mailbox for inter-agent messaging within a team.

Each agent has an inbox directory. Messages are individual JSON files named
<timestamp_ns>_<uuid8>.json written atomically via a temp file + os.rename().
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from operator_use.team.types import MailboxMessage


class TeamMailbox:
    def __init__(self, base: Path, team_name: str, agent_id: str) -> None:
        self._inbox = base / team_name / "agents" / agent_id / "inbox"
        self._inbox.mkdir(parents=True, exist_ok=True)

    async def send(self, type: str, sender_id: str, content: str) -> str:
        """Write a message to this inbox. Returns the message id."""
        msg_id = uuid.uuid4().hex[:8]
        msg = MailboxMessage(
            id=msg_id,
            type=type,
            sender_id=sender_id,
            content=content,
            created_at=time.time(),
        )
        filename = f"{time.time_ns()}_{msg_id}.json"
        tmp = self._inbox / f".{filename}.tmp"
        final = self._inbox / filename
        data = json.dumps({
            "id": msg.id,
            "type": msg.type,
            "sender_id": msg.sender_id,
            "content": msg.content,
            "created_at": msg.created_at,
        })
        tmp.write_text(data, encoding="utf-8")
        os.rename(tmp, final)
        return msg_id

    async def receive(self) -> list[MailboxMessage]:
        """Return all pending messages and remove them from the inbox.

        Reads and deletes each file in a single pass over one listing, so a
        message that arrives after the listing is not deleted before it has
        been read (a separate delete-all glob would lose it).
        """
        msgs: list[MailboxMessage] = []
        for path in sorted(self._inbox.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                msgs.append(MailboxMessage(
                    id=data["id"],
                    type=data["type"],
                    sender_id=data["sender_id"],
                    content=data["content"],
                    created_at=data["created_at"],
                ))
            except Exception:
                pass
            path.unlink(missing_ok=True)
        return msgs

    async def peek(self) -> list[MailboxMessage]:
        """Return all pending messages without removing them."""
        return self._read_all()

    def _read_all(self) -> list[MailboxMessage]:
        msgs: list[MailboxMessage] = []
        for path in sorted(self._inbox.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                msgs.append(MailboxMessage(
                    id=data["id"],
                    type=data["type"],
                    sender_id=data["sender_id"],
                    content=data["content"],
                    created_at=data["created_at"],
                ))
            except Exception:
                pass
        return msgs

"""Session store service."""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from operator_use.messages.service import BaseMessage
from operator_use.utils.helper import ensure_directory
from operator_use.session.views import Session


class SessionStore:
    """Store for sessions, keyed by session id. Persists to JSONL files."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.sessions_dir = ensure_directory(self.workspace / "sessions")
        self._sessions: dict[str, Session] = {}

    def _session_id_to_filename(self, session_id: str) -> str:
        """Make session_id filesystem-safe (e.g. `:` invalid on Windows)."""
        return session_id.replace(":", "_")

    def _sessions_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{self._session_id_to_filename(session_id)}.jsonl"

    def load(self, session_id: str) -> Session | None:
        path = self._sessions_path(session_id)
        if not path.exists():
            return None
        messages: list[BaseMessage] = []
        created_at = datetime.now()
        updated_at = datetime.now()
        metadata: dict[str, Any] = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line.strip())
                if obj.get("type") == "metadata":
                    if ts := obj.get("created_at"):
                        created_at = datetime.fromisoformat(ts)
                    if ts := obj.get("updated_at"):
                        updated_at = datetime.fromisoformat(ts)
                    metadata = obj.get("metadata", {})
                    continue
                if "role" in obj:
                    messages.append(BaseMessage.from_dict(obj))
        return Session(
            id=session_id,
            messages=messages,
            created_at=created_at,
            updated_at=updated_at,
            metadata=metadata,
        )

    def save(self, session: Session) -> None:
        path = self._sessions_path(session.id)
        with open(path, "w", encoding="utf-8") as f:
            meta = {
                "type": "metadata",
                "id": session.id,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "metadata": session.metadata,
            }
            f.write(json.dumps(meta) + "\n")
            for msg in session.messages:
                f.write(json.dumps(msg.to_dict()) + "\n")

    def get_or_create(self, session_id: str | None = None) -> Session:
        """Get a session by id, or create and store a new one. Loads from JSONL if exists."""
        id = session_id or str(uuid.uuid4())
        if session := self._sessions.get(id):
            return session
        if session := self.load(id):
            self._sessions[id] = session
            return session
        session = Session(id=id)
        self._sessions[id] = session
        return session

    def delete(self, session_id: str) -> bool:
        """Delete a session. Returns True if it existed."""
        path = self._sessions_path(session_id)
        if session_id in self._sessions:
            del self._sessions[session_id]
        if path.exists():
            path.unlink()
            return True
        return False

    def archive(self, session_id: str) -> bool:
        """Archive a session by renaming its file with a timestamp suffix. Returns True if existed.

        The active session slot is freed so the next get_or_create starts fresh.
        """
        path = self._sessions_path(session_id)
        if session_id in self._sessions:
            del self._sessions[session_id]
        if path.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_name = f"{self._session_id_to_filename(session_id)}_archived_{timestamp}.jsonl"
            path.rename(self.sessions_dir / archive_name)
            return True
        return False

    def list_sessions(self) -> list[dict[str, Any]]:
        """Load sessions from the sessions directory. Returns list of dicts with id, created_at, updated_at, path."""
        result: list[dict[str, Any]] = []
        for path in self.sessions_dir.glob("*.jsonl"):
            session_id = path.stem
            created_at = ""
            updated_at = ""
            try:
                with open(path, encoding="utf-8") as f:
                    first = f.readline()
                    if first.strip():
                        obj = json.loads(first)
                        if obj.get("type") == "metadata" or "role" not in obj:
                            created_at = obj.get("created_at", "")
                            updated_at = obj.get("updated_at", "")
            except (json.JSONDecodeError, OSError):
                pass
            result.append(
                {
                    "id": session_id,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "path": str(path),
                }
            )
        return result

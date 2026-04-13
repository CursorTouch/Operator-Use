"""Session store service."""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from operator_use.messages.service import BaseMessage
from operator_use.utils.helper import ensure_directory
from operator_use.session.views import Session, DEFAULT_SESSION_TTL


class SessionStore:
    """Store for sessions, keyed by session id. Persists to JSONL files.

    When *encryption_key* is provided (a URL-safe base-64 Fernet key), session
    files are written as a single encrypted blob instead of plain JSONL lines.
    The key can be generated with ``cryptography.fernet.Fernet.generate_key()``.
    """

    def __init__(self, workspace: Path, encryption_key: Optional[str] = None):
        self.workspace = Path(workspace)
        self.sessions_dir = ensure_directory(self.workspace / "sessions")
        self._sessions: dict[str, Session] = {}
        self._fernet = None
        if encryption_key:
            from cryptography.fernet import Fernet
            key_bytes = encryption_key.encode() if isinstance(encryption_key, str) else encryption_key
            self._fernet = Fernet(key_bytes)

    def _session_id_to_filename(self, session_id: str) -> str:
        """Make session_id filesystem-safe (e.g. `:` invalid on Windows)."""
        return session_id.replace(":", "_")

    def _sessions_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{self._session_id_to_filename(session_id)}.jsonl"

    def load(self, session_id: str, ttl: float = DEFAULT_SESSION_TTL) -> Session | None:
        path = self._sessions_path(session_id)
        if not path.exists():
            return None

        if self._fernet:
            return self._load_encrypted(session_id, path, ttl)

        raw = path.read_bytes()
        if raw.startswith(b"gAAAAA") and self._fernet is None:
            raise ValueError(
                f"Session file for '{session_id}' is Fernet-encrypted but no encryption_key was provided."
            )

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

        return Session._from_persisted(
            id=session_id,
            messages=messages,
            created_at=created_at,
            updated_at=updated_at,
            metadata=metadata,
            ttl=ttl,
        )

    def _load_encrypted(self, session_id: str, path: Path, ttl: float) -> Session | None:
        """Load and decrypt a session file written by _save_encrypted()."""
        if self._fernet is None:
            raise ValueError(
                f"Session {session_id!r} appears to be encrypted but no encryption_key was provided."
            )
        raw = path.read_bytes()
        try:
            decrypted = self._fernet.decrypt(raw)
        except Exception as exc:
            raise ValueError(
                f"Failed to decrypt session {session_id!r}. "
                "Ensure the correct encryption_key is configured."
            ) from exc

        payload = json.loads(decrypted.decode())
        created_at = datetime.fromisoformat(payload.get("created_at", datetime.now().isoformat()))
        updated_at = datetime.fromisoformat(payload.get("updated_at", datetime.now().isoformat()))
        metadata = payload.get("metadata", {})
        messages = [BaseMessage.from_dict(m) for m in payload.get("messages", [])]
        return Session._from_persisted(
            id=session_id,
            messages=messages,
            created_at=created_at,
            updated_at=updated_at,
            metadata=metadata,
            ttl=ttl,
        )

    def save(self, session: Session) -> None:
        path = self._sessions_path(session.id)

        if self._fernet:
            self._save_encrypted(session, path)
            return

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

    def _save_encrypted(self, session: Session, path: Path) -> None:
        """Serialize the session to JSON and write as a Fernet-encrypted blob."""
        payload = {
            "id": session.id,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata,
            "messages": [msg.to_dict() for msg in session.messages],
        }
        token = self._fernet.encrypt(json.dumps(payload).encode())
        path.write_bytes(token)

    def get_or_create(
        self,
        session_id: Optional[str] = None,
        ttl: float = DEFAULT_SESSION_TTL,
    ) -> Session:
        """Get a session by id, or create and store a new one.

        Loads from JSONL if exists. If the loaded session is expired (based on
        real idle time derived from *updated_at*), it is deleted and a fresh
        session is returned instead.
        """
        id = session_id or str(uuid.uuid4())

        if cached := self._sessions.get(id):
            if not cached.is_expired():
                return cached
            # In-memory session has expired — evict and fall through to create
            del self._sessions[id]

        if session := self.load(id, ttl=ttl):
            if session.is_expired():
                self.delete(id)
            else:
                self._sessions[id] = session
                return session

        session = Session(id=id, ttl=ttl)
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

    def cleanup(self, ttl: float = DEFAULT_SESSION_TTL) -> list[str]:
        """Delete all sessions whose idle time (since *updated_at*) exceeds *ttl*.

        Returns the list of session IDs (filesystem-safe stems) that were removed.
        Archived session files are skipped.
        """
        removed: list[str] = []
        for path in self.sessions_dir.glob("*.jsonl"):
            # Skip archived sessions
            if "_archived_" in path.stem:
                continue
            session_id_fs = path.stem
            session = self.load(session_id_fs, ttl=ttl)
            if session is None:
                continue
            if session.is_expired():
                path.unlink()
                if session_id_fs in self._sessions:
                    del self._sessions[session_id_fs]
                removed.append(session_id_fs)
        return removed

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

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from program.auth.storage import FileAuthStorage
from program.auth.types import LockResult

logger = logging.getLogger(__name__)


class ACPAuthManager:
    """
    Manages ACP agent credentials persisted in ~/.program/auth/acp.json.

    File structure::

        {
            "codex":       { "token": "...", "added_at": "2026-05-18T..." },
            "claude-code": { "token": "...", "added_at": "..." }
        }

    Token lifecycle:
        - set_token(name, token)   — called after device-flow approval
        - delete_token(name)       — revoke / forget an agent
        - get_token(name)          — retrieve token before connecting
    """

    def __init__(self, auth_path: Path) -> None:
        self._storage = FileAuthStorage(auth_path)

    # ── Internal read/write ───────────────────────────────────────────────────

    def _read(self) -> dict[str, dict]:
        result = self._storage.with_lock(lambda current: LockResult(result=current))
        try:
            return json.loads(result.result or '{}')
        except Exception:
            return {}

    def _write(self, data: dict[str, dict]) -> None:
        self._storage.with_lock(
            lambda _: LockResult(result=None, next=json.dumps(data, indent=2))
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def get_token(self, agent_name: str) -> str | None:
        return self._read().get(agent_name, {}).get('token')

    def has_token(self, agent_name: str) -> bool:
        return agent_name in self._read()

    def set_token(self, agent_name: str, token: str) -> None:
        data = self._read()
        data[agent_name] = {
            'token': token,
            'added_at': datetime.now(timezone.utc).isoformat(),
        }
        self._write(data)
        logger.info('ACP: stored token for agent %r', agent_name)

    def delete_token(self, agent_name: str) -> bool:
        """Delete token and return True if it existed."""
        data = self._read()
        if agent_name not in data:
            return False
        del data[agent_name]
        self._write(data)
        logger.info('ACP: removed token for agent %r', agent_name)
        return True

    def list_agents(self) -> list[str]:
        return list(self._read().keys())

    def get_entry(self, agent_name: str) -> dict | None:
        return self._read().get(agent_name)

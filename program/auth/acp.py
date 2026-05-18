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
    Manages ACP agent credentials persisted in the `acp` section of auth.json.

    auth.json structure (coexists with provider credentials)::

        {
            "anthropic": { "type": "api_key", "key": "sk-..." },
            "acp": {
                "codex": { "token": "...", "added_at": "2026-05-18T..." },
                "claude-code": { "token": "...", "added_at": "..." }
            }
        }

    Token lifecycle:
        - set_token(name, token)   — called after device-flow approval
        - delete_token(name)       — revoke / forget an agent
        - get_token(name)          — retrieve token before connecting
    """

    def __init__(self, auth_path: Path) -> None:
        self._storage = FileAuthStorage(auth_path)

    # ── Internal read/write ───────────────────────────────────────────────────

    def _read_acp(self) -> dict[str, dict]:
        result = self._storage.with_lock(lambda current: LockResult(result=current))
        try:
            data = json.loads(result.result or '{}')
        except Exception:
            data = {}
        return data.get('acp', {})

    def _write_acp(self, acp: dict[str, dict]) -> None:
        def update(current: str | None) -> LockResult:
            try:
                data = json.loads(current or '{}')
            except Exception:
                data = {}
            data['acp'] = acp
            return LockResult(result=None, next=json.dumps(data, indent=2))
        self._storage.with_lock(update)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_token(self, agent_name: str) -> str | None:
        return self._read_acp().get(agent_name, {}).get('token')

    def has_token(self, agent_name: str) -> bool:
        return agent_name in self._read_acp()

    def set_token(self, agent_name: str, token: str) -> None:
        acp = self._read_acp()
        acp[agent_name] = {
            'token': token,
            'added_at': datetime.now(timezone.utc).isoformat(),
        }
        self._write_acp(acp)
        logger.info('ACP: stored token for agent %r', agent_name)

    def delete_token(self, agent_name: str) -> bool:
        """Delete token and return True if it existed."""
        acp = self._read_acp()
        if agent_name not in acp:
            return False
        del acp[agent_name]
        self._write_acp(acp)
        logger.info('ACP: removed token for agent %r', agent_name)
        return True

    def list_agents(self) -> list[str]:
        return list(self._read_acp().keys())

    def get_entry(self, agent_name: str) -> dict | None:
        return self._read_acp().get(agent_name)

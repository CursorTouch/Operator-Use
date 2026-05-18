from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class ACPManager:
    """
    Per-agent ACP session persistence.

    One JSON file per registered agent::

        ~/.program/agent/acp/codex.json
        ~/.program/agent/acp/claude-code.json

    Session lifecycle:
        - Created on first successful ACP connection to an agent.
        - Updated (last_used_at) on every subsequent call.
        - Deleted when the agent's auth token is revoked.

    Session files store the session_id so that follow-up tasks reach the
    same context on the remote agent (where the transport supports it).
    For stdio agents, the session_id is connection-scoped and will be
    recreated automatically when the process restarts.
    """

    def __init__(self, sessions_dir: Path) -> None:
        self._dir = sessions_dir
        self._dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, agent_name: str) -> Path:
        safe = agent_name.replace('/', '_').replace('\\', '_')
        return self._dir / f'{safe}.json'

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, agent_name: str) -> dict | None:
        """Return session dict or None if no session exists."""
        p = self._path(agent_name)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except Exception:
            return None

    def get_session_id(self, agent_name: str) -> str | None:
        entry = self.get(agent_name)
        return entry['session_id'] if entry else None

    def save(self, agent_name: str, session_id: str, cwd: str | None = None) -> None:
        """Create or update the session file for an agent."""
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get(agent_name) or {}
        data = {
            'agent_name': agent_name,
            'session_id': session_id,
            'cwd': cwd or existing.get('cwd'),
            'created_at': existing.get('created_at', now),
            'last_used_at': now,
        }
        p = self._path(agent_name)
        p.write_text(json.dumps(data, indent=2))
        p.chmod(0o600)

    def delete(self, agent_name: str) -> bool:
        """Delete the session file and return True if it existed."""
        p = self._path(agent_name)
        if p.exists():
            p.unlink()
            logger.info('ACP session deleted for %r', agent_name)
            return True
        return False

    def list(self) -> list[dict]:
        """Return all stored session dicts."""
        result = []
        for p in sorted(self._dir.glob('*.json')):
            try:
                result.append(json.loads(p.read_text()))
            except Exception:
                pass
        return result

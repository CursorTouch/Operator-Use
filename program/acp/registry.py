from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

_REGISTRY_PATH = os.path.expanduser('~/.operator/acp-registry.json')
_ENTRY_TTL = 60  # seconds — stale entries older than this are ignored


class ACPRegistry:
    """
    Local file-based registry for Operator ACP agent instances running on
    the same machine.  Entries are written by the server on startup and
    removed on shutdown.  Clients call ``find()`` to discover an agent by
    its ``agent_id`` and read back the transport parameters needed to connect.

    Registry file: ``~/.operator/acp-registry.json``

    Entry schema::

        {
            "agent_id": "operator",
            "transport": "stdio",       # "stdio" | "unix" | "http"
            "command": "operator",      # stdio: executable
            "args": ["acp"],            # stdio: extra args
            "socket": "/tmp/op.sock",   # unix: socket path
            "url": "http://...",        # http: base URL
            "pid": 12345,
            "registered_at": 1716000000.0
        }
    """

    def __init__(self, path: str = _REGISTRY_PATH) -> None:
        self._path = path

    # ── Public API ────────────────────────────────────────────────────────────

    def register(self, entry: dict[str, Any]) -> None:
        """Add or update an entry in the registry (write at startup)."""
        entry = {**entry, 'registered_at': time.time()}
        records = self._load()
        records[entry['agent_id']] = entry
        self._save(records)
        logger.debug('ACPRegistry: registered %r', entry['agent_id'])

    def unregister(self, agent_id: str) -> None:
        """Remove an entry from the registry (call at shutdown)."""
        records = self._load()
        if agent_id in records:
            del records[agent_id]
            self._save(records)
            logger.debug('ACPRegistry: unregistered %r', agent_id)

    async def find(self, agent_id: str) -> dict[str, Any] | None:
        """Return the registry entry for ``agent_id``, or None if not found/stale."""
        records = self._load()
        entry = records.get(agent_id)
        if entry is None:
            return None
        age = time.time() - entry.get('registered_at', 0)
        if age > _ENTRY_TTL:
            logger.debug('ACPRegistry: entry %r is stale (%.0fs old)', agent_id, age)
            return None
        # Verify the process is still alive for stdio/unix transports
        pid = entry.get('pid')
        if pid and not _pid_alive(pid):
            logger.debug('ACPRegistry: entry %r pid %d is dead', agent_id, pid)
            self.unregister(agent_id)
            return None
        return entry

    def list_agents(self) -> list[dict[str, Any]]:
        """Return all live registry entries."""
        records = self._load()
        now = time.time()
        live = []
        for entry in records.values():
            age = now - entry.get('registered_at', 0)
            if age > _ENTRY_TTL:
                continue
            pid = entry.get('pid')
            if pid and not _pid_alive(pid):
                continue
            live.append(entry)
        return live

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        if not os.path.exists(self._path):
            return {}
        try:
            with open(self._path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as exc:
            logger.warning('ACPRegistry: failed to load %r: %s', self._path, exc)
            return {}

    def _save(self, records: dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        tmp = self._path + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(records, f, indent=2)
            os.replace(tmp, self._path)
        except Exception as exc:
            logger.error('ACPRegistry: failed to save %r: %s', self._path, exc)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

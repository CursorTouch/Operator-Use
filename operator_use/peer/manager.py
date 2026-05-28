"""PeerSessionManager — bookmark store for peer-to-peer agent sessions.

Mirrors ACPSessionManager.  One small JSON file per peer profile:

    profiles/alice/peer/bob.json
    profiles/alice/peer/charlie.json

The file records the session_id (UUID) of the last conversation Alice had
with that peer.  The actual JSONL history lives on the peer's side under:

    profiles/bob/p2p/alice/<timestamp>_<session_id>.jsonl

On resume Alice reads her bookmark → gets the session_id → Bob's agent finds
the matching file in his p2p/alice/ dir and loads the full conversation.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class PeerSessionManager:
    """
    Manages peer-agent session bookmarks for one profile.

    Each bookmark is a tiny JSON file that records which session_id to resume
    when this profile talks to a named peer again.
    """

    def __init__(self, peer_dir: Path | None) -> None:
        self._dir = peer_dir
        if peer_dir is not None:
            peer_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, profile_name: str) -> Path | None:
        if self._dir is None:
            return None
        safe = profile_name.replace('/', '_').replace('\\', '_')
        return self._dir / f'{safe}.json'

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, profile_name: str) -> dict | None:
        """Return the bookmark dict for a peer, or None if none exists."""
        p = self._path(profile_name)
        if p is None or not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            return None

    def get_session_id(self, profile_name: str) -> str | None:
        entry = self.get(profile_name)
        return entry['session_id'] if entry else None

    def save(self, profile_name: str, session_id: str) -> None:
        """Create or update the bookmark for a peer.  No-op when no peer dir is set."""
        p = self._path(profile_name)
        if p is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get(profile_name) or {}
        data = {
            'profile': profile_name,
            'session_id': session_id,
            'created_at': existing.get('created_at', now),
            'last_used_at': now,
        }
        p.write_text(json.dumps(data, indent=2), encoding='utf-8')
        p.chmod(0o600)

    def delete(self, profile_name: str) -> bool:
        """Delete the bookmark and return True if it existed."""
        p = self._path(profile_name)
        if p is None:
            return False
        if p.exists():
            p.unlink()
            logger.info('Peer session bookmark deleted for %r', profile_name)
            return True
        return False

    def list(self) -> list[dict]:
        """Return all stored bookmarks."""
        if self._dir is None:
            return []
        result = []
        for p in sorted(self._dir.glob('*.json')):
            try:
                result.append(json.loads(p.read_text(encoding='utf-8')))
            except Exception:
                pass
        return result

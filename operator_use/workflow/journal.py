from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class WorkflowJournal:
    """Write-through cache keyed by (prompt, opts) SHA-256. Persists to disk."""

    def __init__(self, run_dir: Path | None = None) -> None:
        self._cache: dict[str, Any] = {}
        self._path = run_dir / 'journal.json' if run_dir else None
        if self._path and self._path.exists():
            try:
                self._cache = json.loads(self._path.read_text())
            except Exception:
                self._cache = {}

    def get(self, prompt: str, opts: dict) -> Any | None:
        return self._cache.get(self._key(prompt, opts))

    def set(self, prompt: str, opts: dict, result: Any) -> None:
        self._cache[self._key(prompt, opts)] = result
        if self._path:
            try:
                self._path.write_text(json.dumps(self._cache, indent=2))
            except Exception:
                pass

    def _key(self, prompt: str, opts: dict) -> str:
        payload = json.dumps({'prompt': prompt, **opts}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

"""Skill usage tracking — sidecar .usage.json in the user skills directory.

Tracks per-skill activity (use, view, patch counts + timestamps) and lifecycle
state (active / stale / archived). Only agent-created skills are tracked here;
builtin skills are never registered.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from program.settings.paths import get_skills_dir

logger = logging.getLogger(__name__)

STATE_ACTIVE   = 'active'
STATE_STALE    = 'stale'
STATE_ARCHIVED = 'archived'

_USAGE_FILE = '.usage.json'


def _usage_path() -> Path:
    return get_skills_dir() / _USAGE_FILE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, Any]:
    path = _usage_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save(data: dict[str, Any]) -> None:
    path = _usage_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
        tmp.replace(path)
    except Exception as exc:
        logger.debug('skill usage save failed: %s', exc)


def _record(data: dict[str, Any], name: str) -> dict[str, Any]:
    if name not in data:
        data[name] = {
            'created_by': 'agent',
            'created_at': _now_iso(),
            'state': STATE_ACTIVE,
            'pinned': False,
            'use_count': 0, 'last_used_at': None,
            'view_count': 0, 'last_viewed_at': None,
            'patch_count': 0, 'last_patched_at': None,
            'archived_at': None,
        }
    return data[name]


# ── Public API ────────────────────────────────────────────────────────────────

def register(name: str) -> None:
    """Register a newly agent-created skill."""
    data = _load()
    if name not in data:
        _record(data, name)
        _save(data)


def record_use(name: str) -> None:
    data = _load()
    rec = _record(data, name)
    rec['use_count'] = rec.get('use_count', 0) + 1
    rec['last_used_at'] = _now_iso()
    _save(data)


def record_view(name: str) -> None:
    data = _load()
    rec = _record(data, name)
    rec['view_count'] = rec.get('view_count', 0) + 1
    rec['last_viewed_at'] = _now_iso()
    _save(data)


def record_patch(name: str) -> None:
    data = _load()
    rec = _record(data, name)
    rec['patch_count'] = rec.get('patch_count', 0) + 1
    rec['last_patched_at'] = _now_iso()
    _save(data)


def set_state(name: str, state: str) -> None:
    data = _load()
    rec = _record(data, name)
    rec['state'] = state
    if state == STATE_ARCHIVED:
        rec['archived_at'] = _now_iso()
    _save(data)


def set_pinned(name: str, pinned: bool) -> None:
    data = _load()
    rec = _record(data, name)
    rec['pinned'] = pinned
    _save(data)


def remove(name: str) -> None:
    data = _load()
    data.pop(name, None)
    _save(data)


def get(name: str) -> dict[str, Any] | None:
    return _load().get(name)


def is_agent_created(name: str) -> bool:
    rec = get(name)
    return rec is not None and rec.get('created_by') == 'agent'


def is_pinned(name: str) -> bool:
    rec = get(name)
    return bool(rec and rec.get('pinned'))


def latest_activity_at(rec: dict[str, Any]) -> datetime | None:
    """Return the most recent of last_used_at / last_viewed_at / last_patched_at."""
    candidates = [
        rec.get('last_used_at'),
        rec.get('last_viewed_at'),
        rec.get('last_patched_at'),
    ]
    parsed = []
    for ts in candidates:
        if ts:
            try:
                parsed.append(datetime.fromisoformat(ts))
            except ValueError:
                pass
    return max(parsed) if parsed else None


def agent_created_report() -> list[dict[str, Any]]:
    """Return all agent-created skill records with their names."""
    data = _load()
    rows = []
    for name, rec in data.items():
        if rec.get('created_by') == 'agent':
            rows.append({'name': name, **rec})
    return rows

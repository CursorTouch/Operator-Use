"""Skill usage tracking — sidecar .usage.json in the profile's skills directory."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATE_ACTIVE   = 'active'
STATE_STALE    = 'stale'
STATE_ARCHIVED = 'archived'

_USAGE_FILE = '.usage.json'


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(skills_dir: Path) -> dict[str, Any]:
    path = skills_dir / _USAGE_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save(skills_dir: Path, data: dict[str, Any]) -> None:
    path = skills_dir / _USAGE_FILE
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

def register(skills_dir: Path, name: str) -> None:
    data = _load(skills_dir)
    if name not in data:
        _record(data, name)
        _save(skills_dir, data)


def record_use(skills_dir: Path, name: str) -> None:
    data = _load(skills_dir)
    rec = _record(data, name)
    rec['use_count'] = rec.get('use_count', 0) + 1
    rec['last_used_at'] = _now_iso()
    _save(skills_dir, data)


def record_view(skills_dir: Path, name: str) -> None:
    data = _load(skills_dir)
    rec = _record(data, name)
    rec['view_count'] = rec.get('view_count', 0) + 1
    rec['last_viewed_at'] = _now_iso()
    _save(skills_dir, data)


def record_patch(skills_dir: Path, name: str) -> None:
    data = _load(skills_dir)
    rec = _record(data, name)
    rec['patch_count'] = rec.get('patch_count', 0) + 1
    rec['last_patched_at'] = _now_iso()
    _save(skills_dir, data)


def set_state(skills_dir: Path, name: str, state: str) -> None:
    data = _load(skills_dir)
    rec = _record(data, name)
    rec['state'] = state
    if state == STATE_ARCHIVED:
        rec['archived_at'] = _now_iso()
    _save(skills_dir, data)


def set_pinned(skills_dir: Path, name: str, pinned: bool) -> None:
    data = _load(skills_dir)
    rec = _record(data, name)
    rec['pinned'] = pinned
    _save(skills_dir, data)


def remove(skills_dir: Path, name: str) -> None:
    data = _load(skills_dir)
    data.pop(name, None)
    _save(skills_dir, data)


def get(skills_dir: Path, name: str) -> dict[str, Any] | None:
    return _load(skills_dir).get(name)


def is_agent_created(skills_dir: Path, name: str) -> bool:
    rec = get(skills_dir, name)
    return rec is not None and rec.get('created_by') == 'agent'


def is_pinned(skills_dir: Path, name: str) -> bool:
    rec = get(skills_dir, name)
    return bool(rec and rec.get('pinned'))


def latest_activity_at(rec: dict[str, Any]) -> datetime | None:
    candidates = [rec.get('last_used_at'), rec.get('last_viewed_at'), rec.get('last_patched_at')]
    parsed = []
    for ts in candidates:
        if ts:
            try:
                parsed.append(datetime.fromisoformat(ts))
            except ValueError:
                pass
    return max(parsed) if parsed else None


def agent_created_report(skills_dir: Path) -> list[dict[str, Any]]:
    data = _load(skills_dir)
    return [{'name': name, **rec} for name, rec in data.items() if rec.get('created_by') == 'agent']

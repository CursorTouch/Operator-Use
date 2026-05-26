"""Disk cache for the skill index.

Stores skill name/description/requires_tools/file_path per source directory.
Cache is invalidated when any SKILL.md mtime changes or files are added/removed.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CACHE_VERSION = 1


def _skill_index_cache_path(cache_dir: Path) -> Path:
    return cache_dir / "skill_index.json"


def _collect_mtimes(dirs: list[Path]) -> dict[str, float]:
    """Return {str(skill_file): mtime} for every SKILL.md under dirs."""
    mtimes: dict[str, float] = {}
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.rglob("SKILL.md"):
            try:
                mtimes[str(p)] = p.stat().st_mtime
            except OSError:
                pass
    return mtimes


def load_skill_index_cache(
    cache_dir: Path,
    scan_dirs: list[Path],
) -> list[dict[str, Any]] | None:
    """Return cached skill dicts if still valid, else None."""
    path = _skill_index_cache_path(cache_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != _CACHE_VERSION:
            return None
        if data.get("mtimes") != _collect_mtimes(scan_dirs):
            return None
        return data.get("skills", [])
    except Exception as exc:
        logger.debug("skill cache load failed: %s", exc)
        return None


def save_skill_index_cache(
    cache_dir: Path,
    scan_dirs: list[Path],
    skills: list[dict[str, Any]],
) -> None:
    """Persist skill index and current mtimes to disk."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _CACHE_VERSION,
            "mtimes": _collect_mtimes(scan_dirs),
            "skills": skills,
        }
        _skill_index_cache_path(cache_dir).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.debug("skill cache save failed: %s", exc)

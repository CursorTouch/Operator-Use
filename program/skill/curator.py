"""Skill curator — periodic lifecycle management for agent-created skills.

Two passes per run:
  1. Automatic (no LLM): mark stale at 30d, archive at 90d, reactivate if re-used.
  2. LLM consolidation: fork a review engine that merges narrow skills into
     class-level umbrellas via skill_manage.

Triggered at session start when:
  - curator.enabled is True (default)
  - curator is not paused
  - last_run_at is older than interval_hours (default: 7 days)
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from program.settings.paths import get_skills_dir
from program.skill import usage as skill_usage
from program.skill.usage import STATE_ACTIVE, STATE_ARCHIVED, STATE_STALE

logger = logging.getLogger(__name__)

_STATE_FILE  = '.curator_state'
_ARCHIVE_DIR = '.archive'

# ── Defaults (overridden by CuratorSettings) ─────────────────────────────────
DEFAULT_INTERVAL_HOURS   = 168   # 7 days
DEFAULT_MIN_IDLE_HOURS   = 2
DEFAULT_STALE_AFTER_DAYS = 30
DEFAULT_ARCHIVE_AFTER_DAYS = 90

_CURATOR_REVIEW_PROMPT = (
    "You are the background skill CURATOR for this agent. "
    "This is a consolidation pass — your goal is CLASS-LEVEL skills.\n\n"
    "A library of hundreds of narrow one-session skills is a FAILURE. "
    "Merge, consolidate, and build umbrella skills.\n\n"
    "Hard rules:\n"
    "1. Only touch agent-created skills (those listed below).\n"
    "2. Never delete — only archive via skill_manage action=delete "
    "(which moves the skill to .archive/, not permanent deletion).\n"
    "3. Never touch pinned skills.\n"
    "4. Judge on CONTENT, not usage counters.\n\n"
    "Preference order:\n"
    "  1. PATCH an existing umbrella skill to absorb the narrow one.\n"
    "  2. CREATE a new umbrella and merge narrow skills into it.\n"
    "  3. WRITE SUPPORT FILES (references/, templates/, scripts/) under "
    "an existing umbrella.\n"
    "  4. ARCHIVE a skill with absorbed_into=<umbrella> when content is "
    "fully absorbed, or absorbed_into='' to prune genuinely stale/irrelevant skills.\n\n"
    "When done, emit a YAML block:\n"
    "```yaml\n"
    "consolidations:\n"
    "  - from: <old-skill>\n"
    "    into: <umbrella>\n"
    "    reason: <one sentence>\n"
    "prunings:\n"
    "  - name: <skill>\n"
    "    reason: <one sentence>\n"
    "```\n\n"
    "If nothing needs consolidation, say 'Nothing to consolidate.' and stop."
)


# ── State file ────────────────────────────────────────────────────────────────

def _state_path() -> Path:
    return get_skills_dir() / _STATE_FILE


def _load_state() -> dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix('.tmp')
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding='utf-8')
        tmp.replace(p)
    except Exception as exc:
        logger.debug('curator state save failed: %s', exc)


# ── Archive helpers ───────────────────────────────────────────────────────────

def archive_skill(name: str) -> bool:
    """Move skill dir to .archive/<name>/. Returns True on success."""
    skills_dir = get_skills_dir()
    src = skills_dir / name
    if not src.is_dir():
        return False
    archive_dir = skills_dir / _ARCHIVE_DIR
    archive_dir.mkdir(parents=True, exist_ok=True)
    dst = archive_dir / name
    if dst.exists():
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        dst = archive_dir / f'{name}-{stamp}'
    try:
        shutil.move(str(src), str(dst))
        skill_usage.set_state(name, STATE_ARCHIVED)
        logger.info('curator: archived skill %r → %s', name, dst)
        return True
    except Exception as exc:
        logger.warning('curator: archive failed for %r: %s', name, exc)
        return False


def restore_skill(name: str) -> tuple[bool, str]:
    """Restore an archived skill back to the skills dir."""
    skills_dir = get_skills_dir()
    archive_dir = skills_dir / _ARCHIVE_DIR
    src = archive_dir / name
    if not src.exists():
        return False, f"'{name}' not found in archive."
    dst = skills_dir / name
    if dst.exists():
        return False, f"Skill '{name}' already exists. Remove it first."
    try:
        shutil.move(str(src), str(dst))
        skill_usage.set_state(name, STATE_ACTIVE)
        logger.info('curator: restored skill %r', name)
        return True, f"Skill '{name}' restored."
    except Exception as exc:
        return False, str(exc)


def list_archived() -> list[str]:
    archive_dir = get_skills_dir() / _ARCHIVE_DIR
    if not archive_dir.is_dir():
        return []
    return [d.name for d in sorted(archive_dir.iterdir()) if d.is_dir()]


# ── Gate: should we run now? ──────────────────────────────────────────────────

def should_run_now(
    interval_hours: int = DEFAULT_INTERVAL_HOURS,
    paused: bool = False,
) -> bool:
    if paused:
        return False
    state = _load_state()
    last_raw = state.get('last_run_at')
    if not last_raw:
        # First-ever run: seed the timestamp, defer
        _save_state({**state, 'last_run_at': datetime.now(timezone.utc).isoformat()})
        return False
    try:
        last = datetime.fromisoformat(last_raw)
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - last) >= timedelta(hours=interval_hours)


# ── Auto-transitions (no LLM) ─────────────────────────────────────────────────

def apply_automatic_transitions(
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    archive_after_days: int = DEFAULT_ARCHIVE_AFTER_DAYS,
) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    stale_cutoff   = now - timedelta(days=stale_after_days)
    archive_cutoff = now - timedelta(days=archive_after_days)
    counts = {'marked_stale': 0, 'archived': 0, 'reactivated': 0}

    for row in skill_usage.agent_created_report():
        name = row['name']
        if row.get('pinned'):
            continue

        last_activity = skill_usage.latest_activity_at(row)
        created_raw = row.get('created_at')
        if created_raw:
            try:
                created = datetime.fromisoformat(created_raw)
            except ValueError:
                created = now
        else:
            created = now
        anchor = last_activity or created

        current = row.get('state', STATE_ACTIVE)

        if anchor <= archive_cutoff and current != STATE_ARCHIVED:
            if archive_skill(name):
                counts['archived'] += 1
        elif anchor <= stale_cutoff and current == STATE_ACTIVE:
            skill_usage.set_state(name, STATE_STALE)
            counts['marked_stale'] += 1
        elif anchor > stale_cutoff and current == STATE_STALE:
            skill_usage.set_state(name, STATE_ACTIVE)
            counts['reactivated'] += 1

    return counts


# ── LLM consolidation pass ────────────────────────────────────────────────────

async def _run_llm_consolidation(llm: Any, skill_manage_tool: Any, skill_view_tool: Any | None) -> str:
    from program.agent.types import AgentContext
    from program.engine.service import Engine
    from program.engine.types import Options
    from program.hooks.types import AgentEndEvent
    from program.message.types import AssistantMessage, UserMessage, TextContent

    rows = skill_usage.agent_created_report()
    active = [r for r in rows if r.get('state') != STATE_ARCHIVED and not r.get('pinned')]
    if not active:
        return 'Nothing to consolidate.'

    candidate_list = '\n'.join(
        f"  - {r['name']} (state={r.get('state','active')}, "
        f"uses={r.get('use_count',0)}, patches={r.get('patch_count',0)})"
        for r in active
    )
    task = (
        f"Agent-created skills available for consolidation:\n{candidate_list}\n\n"
        + _CURATOR_REVIEW_PROMPT
    )

    tools: list[Any] = [skill_manage_tool]
    if skill_view_tool:
        tools.append(skill_view_tool)

    engine = Engine(llm=llm, tools=tools, options=Options())
    ctx = AgentContext(
        system_prompt=(
            'You are the background skill curator. '
            'Consolidate narrow skills into class-level umbrellas.'
        ),
        messages=[UserMessage(contents=[TextContent(content=task)])],
        tools=tools,
    )

    summary = ''

    async def on_event(event: Any) -> None:
        nonlocal summary
        if isinstance(event, AgentEndEvent):
            for msg in reversed(event.messages):
                if isinstance(msg, AssistantMessage):
                    summary = msg.text_content().strip()
                    break

    engine.options.on_event = on_event
    try:
        await asyncio.wait_for(engine.run(ctx), timeout=300.0)
    except asyncio.TimeoutError:
        logger.warning('curator LLM pass timed out')
    except Exception as exc:
        logger.warning('curator LLM pass error: %s', exc)

    return summary or 'Nothing to consolidate.'


# ── Full curator run ──────────────────────────────────────────────────────────

async def run_curator(
    llm: Any,
    skill_manage_tool: Any,
    skill_view_tool: Any | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    archive_after_days: int = DEFAULT_ARCHIVE_AFTER_DAYS,
) -> str:
    counts = apply_automatic_transitions(stale_after_days, archive_after_days)
    auto_summary = (
        f"auto: {counts['marked_stale']} marked stale, "
        f"{counts['archived']} archived, "
        f"{counts['reactivated']} reactivated"
    )
    logger.info('curator auto-transitions: %s', auto_summary)

    llm_summary = await _run_llm_consolidation(llm, skill_manage_tool, skill_view_tool)

    full_summary = f"{auto_summary}; llm: {llm_summary[:120]}"

    state = _load_state()
    _save_state({
        **state,
        'last_run_at': datetime.now(timezone.utc).isoformat(),
        'last_run_summary': full_summary,
        'run_count': state.get('run_count', 0) + 1,
    })
    return full_summary


def maybe_run_curator(
    llm: Any,
    skill_manage_tool: Any,
    skill_view_tool: Any | None = None,
    interval_hours: int = DEFAULT_INTERVAL_HOURS,
    paused: bool = False,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    archive_after_days: int = DEFAULT_ARCHIVE_AFTER_DAYS,
) -> None:
    """Spawn a daemon thread to run the curator if the interval has elapsed."""
    if not should_run_now(interval_hours=interval_hours, paused=paused):
        return

    def _thread_target() -> None:
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                run_curator(llm, skill_manage_tool, skill_view_tool,
                            stale_after_days, archive_after_days)
            )
            logger.info('curator run complete: %s', result)
        except Exception as exc:
            logger.warning('curator thread error: %s', exc)
        finally:
            loop.close()

    t = threading.Thread(target=_thread_target, daemon=True, name='skill-curator')
    t.start()
    logger.debug('skill curator thread spawned')


# ── Status helper ─────────────────────────────────────────────────────────────

def curator_status() -> dict[str, Any]:
    state = _load_state()
    rows = skill_usage.agent_created_report()
    by_state: dict[str, int] = {STATE_ACTIVE: 0, STATE_STALE: 0, STATE_ARCHIVED: 0}
    for r in rows:
        s = r.get('state', STATE_ACTIVE)
        by_state[s] = by_state.get(s, 0) + 1
    return {
        'last_run_at': state.get('last_run_at'),
        'last_run_summary': state.get('last_run_summary'),
        'run_count': state.get('run_count', 0),
        'paused': state.get('paused', False),
        'skills': by_state,
        'archived': len(list_archived()),
    }

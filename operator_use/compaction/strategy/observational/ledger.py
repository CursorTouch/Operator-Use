from __future__ import annotations

from typing import TYPE_CHECKING, Any

from operator_use.compaction.strategy.observational.types import (
    Observation, Reflection, LedgerFold,
    OM_OBSERVATIONS_RECORDED, OM_REFLECTIONS_RECORDED, OM_OBSERVATIONS_DROPPED,
)
from operator_use.compaction.strategy.utils import estimate_tokens

if TYPE_CHECKING:
    from operator_use.session.types import SessionEntry

_SUMMARY_HEADER = (
    "These are condensed memories from earlier in this session.\n\n"
    "- Reflections: stable, long-lived facts about the user, project, decisions, and constraints.\n"
    "- Observations: timestamped events from the conversation history, in chronological order.\n\n"
    "Treat these as past records. When entries conflict, the most recent observation reflects "
    "the latest known state. Work that prior observations describe as completed should not be "
    "redone unless the user explicitly asks to revisit it."
)


def is_source_entry(entry: SessionEntry) -> bool:
    from operator_use.session.types import MessageEntry, CustomMessageEntry, BranchEntry, CompactionEntry
    return isinstance(entry, (MessageEntry, CustomMessageEntry, BranchEntry, CompactionEntry))


def _get_message(entry: SessionEntry) -> Any:
    from operator_use.session.types import MessageEntry
    if isinstance(entry, MessageEntry):
        return entry.message
    return None


def fold_ledger(entries: list[SessionEntry]) -> LedgerFold:
    from operator_use.session.types import CustomInfoEntry

    observations: dict[str, Observation] = {}
    reflections: dict[str, Reflection] = {}
    dropped_ids: set[str] = set()
    latest_obs_coverage: str | None = None
    latest_ref_coverage: str | None = None

    for entry in entries:
        if not isinstance(entry, CustomInfoEntry):
            continue
        data = entry.data
        if not isinstance(data, dict):
            continue

        ct = entry.custom_type
        if ct == OM_OBSERVATIONS_RECORDED:
            if data.get("covers_up_to_id"):
                latest_obs_coverage = data["covers_up_to_id"]
            for obs_dict in data.get("observations") or []:
                try:
                    obs = Observation.from_dict(obs_dict)
                    observations[obs.id] = obs
                except Exception:
                    pass

        elif ct == OM_OBSERVATIONS_DROPPED:
            if data.get("covers_up_to_id"):
                latest_obs_coverage = data["covers_up_to_id"]
            for oid in data.get("observation_ids") or []:
                dropped_ids.add(str(oid))

        elif ct == OM_REFLECTIONS_RECORDED:
            if data.get("covers_up_to_id"):
                latest_ref_coverage = data["covers_up_to_id"]
            for ref_dict in data.get("reflections") or []:
                try:
                    ref = Reflection.from_dict(ref_dict)
                    reflections[ref.id] = ref
                except Exception:
                    pass

    active = [obs for oid, obs in observations.items() if oid not in dropped_ids]
    return LedgerFold(
        active_observations=active,
        reflections=list(reflections.values()),
        latest_observation_coverage_id=latest_obs_coverage,
        latest_reflection_coverage_id=latest_ref_coverage,
    )


def _source_entries_after(entries: list[SessionEntry], entry_id: str | None) -> list[SessionEntry]:
    if entry_id is None:
        return [e for e in entries if is_source_entry(e)]
    found = False
    result = []
    for entry in entries:
        if found and is_source_entry(entry):
            result.append(entry)
        if entry.id == entry_id:
            found = True
    return result


def source_tokens_since_observation_coverage(entries: list[SessionEntry]) -> int:
    fold = fold_ledger(entries)
    after = _source_entries_after(entries, fold.latest_observation_coverage_id)
    return sum(
        estimate_tokens(msg)
        for e in after
        if (msg := _get_message(e)) is not None
    )


def source_tokens_since_reflection_coverage(entries: list[SessionEntry]) -> int:
    fold = fold_ledger(entries)
    after = _source_entries_after(entries, fold.latest_reflection_coverage_id)
    return sum(
        estimate_tokens(msg)
        for e in after
        if (msg := _get_message(e)) is not None
    )


def render_summary(observations: list[Observation], reflections: list[Reflection]) -> str:
    if not observations and not reflections:
        return ""
    parts = [_SUMMARY_HEADER]
    if reflections:
        lines = "\n".join(f"[{r.id}] {r.content}" for r in reflections)
        parts.append(f"## Reflections\n{lines}")
    if observations:
        lines = "\n".join(
            f"[{o.id}] {o.timestamp} [{o.relevance}] {o.content}"
            for o in observations
        )
        parts.append(f"## Observations\n{lines}")
    return "\n\n".join(parts)


def build_compaction_summary(entries: list[SessionEntry], first_kept_id: str) -> str | None:
    boundary: list[SessionEntry] = []
    for entry in entries:
        boundary.append(entry)
        if entry.id == first_kept_id:
            break
    fold = fold_ledger(boundary)
    summary = render_summary(fold.active_observations, fold.reflections)
    return summary if summary else None

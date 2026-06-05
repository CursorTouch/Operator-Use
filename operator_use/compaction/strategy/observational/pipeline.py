from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from operator_use.compaction.strategy.observational.ledger import (
    build_compaction_summary, fold_ledger,
    source_tokens_since_observation_coverage,
    source_tokens_since_reflection_coverage,
)
from operator_use.compaction.strategy.observational.types import (
    OM_OBSERVATIONS_DROPPED, OM_OBSERVATIONS_RECORDED, OM_REFLECTIONS_RECORDED,
)

if TYPE_CHECKING:
    from operator_use.session.types import SessionEntry

_log = logging.getLogger(__name__)


@dataclass
class ObservationPipelineConfig:
    observe_after_tokens: int = 10_000
    reflect_after_tokens: int = 20_000
    pool_target_tokens: int = 10_000
    pool_max_tokens: int = 20_000
    enabled: bool = True


class ObservationPipeline:
    def __init__(self, config: ObservationPipelineConfig, llm: Any) -> None:
        self._config = config
        self._llm = llm
        self._in_flight: bool = False
        self._last_error: str | None = None

    @property
    def in_flight(self) -> bool:
        return self._in_flight

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def should_run(self, entries: list[SessionEntry]) -> bool:
        if not self._config.enabled or self._in_flight:
            return False
        obs_tokens = source_tokens_since_observation_coverage(entries)
        ref_tokens = source_tokens_since_reflection_coverage(entries)
        return (
            obs_tokens >= self._config.observe_after_tokens
            or ref_tokens >= self._config.reflect_after_tokens
        )

    def build_compaction_summary(self, entries: list[SessionEntry], first_kept_id: str) -> str | None:
        return build_compaction_summary(entries, first_kept_id)

    def spawn_pipeline(self, session_manager: Any) -> None:
        if self._in_flight:
            return
        self._in_flight = True
        entries = list(session_manager.get_branch())

        def _thread_target() -> None:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    asyncio.wait_for(
                        _run_pipeline(self._llm, entries, session_manager, self._config),
                        timeout=120.0,
                    )
                )
                self._last_error = None
            except asyncio.TimeoutError:
                _log.warning("observational memory pipeline timed out")
                self._last_error = "timed out"
            except Exception as exc:
                _log.warning("observational memory pipeline error: %s", exc)
                self._last_error = str(exc)
            finally:
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                finally:
                    loop.close()
                self._in_flight = False

        t = threading.Thread(target=_thread_target, daemon=True, name="obs-pipeline")
        t.start()
        _log.debug("observational memory pipeline thread spawned")

    def status_dict(self, entries: list[SessionEntry]) -> dict:
        fold = fold_ledger(entries)
        obs_tokens = source_tokens_since_observation_coverage(entries)
        ref_tokens = source_tokens_since_reflection_coverage(entries)
        return {
            "enabled": self._config.enabled,
            "in_flight": self._in_flight,
            "last_error": self._last_error,
            "observations": len(fold.active_observations),
            "reflections": len(fold.reflections),
            "pool_tokens": sum(o.token_count for o in fold.active_observations),
            "reflection_tokens": sum(r.token_count for r in fold.reflections),
            "tokens_since_obs_coverage": obs_tokens,
            "tokens_since_ref_coverage": ref_tokens,
            "observe_threshold": self._config.observe_after_tokens,
            "reflect_threshold": self._config.reflect_after_tokens,
            "latest_obs_coverage_id": fold.latest_observation_coverage_id,
            "latest_ref_coverage_id": fold.latest_reflection_coverage_id,
        }


async def _run_pipeline(
    llm: Any,
    entries: list[SessionEntry],
    session_manager: Any,
    config: ObservationPipelineConfig,
) -> None:
    from operator_use.compaction.strategy.observational.workers import run_dropper, run_observer, run_reflector
    from operator_use.compaction.strategy.utils import serialize_conversation
    from operator_use.session.types import MessageEntry

    fold = fold_ledger(entries)
    obs_tokens = source_tokens_since_observation_coverage(entries)
    ref_tokens = source_tokens_since_reflection_coverage(entries)

    run_obs = obs_tokens >= config.observe_after_tokens
    run_ref = (not run_obs) and ref_tokens >= config.reflect_after_tokens

    if run_obs:
        coverage_id = fold.latest_observation_coverage_id
        chunk_entries = _source_entries_after(entries, coverage_id)
        source_entry_ids = [e.id for e in chunk_entries]
        messages = [e.message for e in chunk_entries if isinstance(e, MessageEntry)]
        chunk_text = serialize_conversation(messages)
        covers_up_to_id = chunk_entries[-1].id if chunk_entries else coverage_id

        new_obs = await run_observer(
            llm=llm,
            chunk_text=chunk_text,
            source_entry_ids=source_entry_ids,
            prior_observations=fold.active_observations,
            prior_reflections=fold.reflections,
        )
        if new_obs:
            session_manager.append_custom_info(
                OM_OBSERVATIONS_RECORDED,
                {"observations": [o.to_dict() for o in new_obs], "covers_up_to_id": covers_up_to_id},
            )
            _log.debug("observer wrote %d observations", len(new_obs))
            fold.active_observations.extend(new_obs)
            fold.latest_observation_coverage_id = covers_up_to_id

    if run_ref or (run_obs and fold.active_observations and ref_tokens >= config.reflect_after_tokens // 2):
        coverage_id = fold.latest_reflection_coverage_id
        obs_after = _source_entries_after(entries, coverage_id)
        covers_up_to_id = obs_after[-1].id if obs_after else coverage_id

        new_refs = await run_reflector(
            llm=llm,
            observations=fold.active_observations,
            reflections=fold.reflections,
        )
        if new_refs:
            session_manager.append_custom_info(
                OM_REFLECTIONS_RECORDED,
                {"reflections": [r.to_dict() for r in new_refs], "covers_up_to_id": covers_up_to_id},
            )
            _log.debug("reflector wrote %d reflections", len(new_refs))
            fold.reflections = new_refs

            pool_tokens = sum(o.token_count for o in fold.active_observations)
            if pool_tokens > config.pool_target_tokens:
                drop_ids = await run_dropper(
                    llm=llm,
                    observations=fold.active_observations,
                    reflections=fold.reflections,
                    target_tokens=config.pool_target_tokens,
                )
                if drop_ids:
                    session_manager.append_custom_info(
                        OM_OBSERVATIONS_DROPPED,
                        {"observation_ids": drop_ids, "covers_up_to_id": covers_up_to_id},
                    )
                    _log.debug("dropper dropped %d observations", len(drop_ids))


def _source_entries_after(entries: list[SessionEntry], entry_id: str | None) -> list[SessionEntry]:
    from operator_use.compaction.strategy.observational.ledger import is_source_entry
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

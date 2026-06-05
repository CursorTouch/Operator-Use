from __future__ import annotations

import json
import logging
from typing import Any

from operator_use.compaction.strategy.observational.prompts import (
    OBSERVER_SYSTEM_PROMPT, OBSERVER_USER_TEMPLATE,
    REFLECTOR_SYSTEM_PROMPT, REFLECTOR_USER_TEMPLATE,
    DROPPER_SYSTEM_PROMPT, DROPPER_USER_TEMPLATE,
)
from operator_use.compaction.strategy.observational.types import Observation, Reflection
from operator_use.inference.types import LLMContext
from operator_use.message.types import TextContent, UserMessage

_log = logging.getLogger(__name__)


async def _invoke_llm(llm: Any, system: str, user: str) -> str:
    events = await llm.invoke(
        LLMContext(
            messages=[UserMessage(contents=[TextContent(content=user)])],
            system_prompt=system,
        )
    )
    raw = ""
    for event in events:
        text = getattr(event, "text", None)
        if isinstance(text, TextContent):
            raw += text.content
    return raw.strip()


def _parse_json_array(raw: str) -> list:
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("no JSON array found")
    return json.loads(raw[start:end + 1])


def _coverage_tier(obs: Observation, reflections: list[Reflection]) -> str:
    cited = {oid for r in reflections for oid in r.supporting_observation_ids}
    if obs.id not in cited:
        return "none"
    count = sum(1 for r in reflections if obs.id in r.supporting_observation_ids)
    return "strong" if count >= 2 else "partial"


async def run_observer(
    llm: Any,
    chunk_text: str,
    source_entry_ids: list[str],
    prior_observations: list[Observation],
    prior_reflections: list[Reflection],
) -> list[Observation] | None:
    prior_obs_text = "\n".join(f"[{o.id}] {o.content}" for o in prior_observations) or "(none)"
    prior_ref_text = "\n".join(f"[{r.id}] {r.content}" for r in prior_reflections) or "(none)"
    user_prompt = OBSERVER_USER_TEMPLATE.format(
        prior_observations=prior_obs_text,
        prior_reflections=prior_ref_text,
        chunk_text=chunk_text[:50_000],
    )
    try:
        raw = await _invoke_llm(llm, OBSERVER_SYSTEM_PROMPT, user_prompt)
        items = _parse_json_array(raw)
    except Exception as exc:
        _log.debug("observer failed: %s", exc)
        return None

    results: list[Observation] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("content"):
            continue
        try:
            results.append(Observation.create(
                content=str(item["content"]),
                relevance=item.get("relevance", "medium"),
                source_entry_ids=list(item.get("source_entry_ids") or source_entry_ids),
            ))
        except Exception as exc:
            _log.debug("observer item error: %s", exc)
    return results


async def run_reflector(
    llm: Any,
    observations: list[Observation],
    reflections: list[Reflection],
) -> list[Reflection] | None:
    existing = "\n".join(f"[{r.id}] {r.content}" for r in reflections) or "(none)"
    annotated = "\n".join(
        f"[{o.id}] ({_coverage_tier(o, reflections)}) [{o.relevance}] {o.content}"
        for o in observations
    ) or "(none)"
    user_prompt = REFLECTOR_USER_TEMPLATE.format(
        existing_reflections=existing,
        annotated_observations=annotated,
    )
    try:
        raw = await _invoke_llm(llm, REFLECTOR_SYSTEM_PROMPT, user_prompt)
        items = _parse_json_array(raw)
    except Exception as exc:
        _log.debug("reflector failed: %s", exc)
        return None

    results: list[Reflection] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("content"):
            continue
        try:
            results.append(Reflection.create(
                content=str(item["content"]),
                supporting_observation_ids=list(item.get("supporting_observation_ids") or []),
            ))
        except Exception as exc:
            _log.debug("reflector item error: %s", exc)
    return results


async def run_dropper(
    llm: Any,
    observations: list[Observation],
    reflections: list[Reflection],
    target_tokens: int,
) -> list[str] | None:
    current_tokens = sum(o.token_count for o in observations)
    free_tokens = max(0, current_tokens - target_tokens)
    annotated = "\n".join(
        f"[{o.id}] ({_coverage_tier(o, reflections)}) [{o.relevance}] cost={o.token_count} {o.content}"
        for o in observations
    ) or "(none)"
    ref_text = "\n".join(f"[{r.id}] {r.content}" for r in reflections) or "(none)"
    user_prompt = DROPPER_USER_TEMPLATE.format(
        current_tokens=current_tokens,
        target_tokens=target_tokens,
        free_tokens=free_tokens,
        annotated_observations=annotated,
        reflections=ref_text,
    )
    try:
        raw = await _invoke_llm(llm, DROPPER_SYSTEM_PROMPT, user_prompt)
        items = _parse_json_array(raw)
    except Exception as exc:
        _log.debug("dropper failed: %s", exc)
        return None
    return [str(i) for i in items if isinstance(i, str)]

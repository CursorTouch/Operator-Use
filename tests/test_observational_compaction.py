"""Tests for ObservationalCompaction: after_turn clock, compact with ledger, fallback."""
from __future__ import annotations

import asyncio
import pytest
from typing import AsyncIterator
from unittest.mock import MagicMock, patch

from operator_use.compaction.strategy.observational.service import ObservationalCompaction
from operator_use.compaction.strategy.types import CompactionSettings, CompactionPreparation, CompactionResult
from operator_use.compaction.strategy.observational.pipeline import ObservationPipeline, ObservationPipelineConfig
from operator_use.compaction.strategy.observational.types import (
    Observation, Reflection,
    OM_OBSERVATIONS_RECORDED, OM_REFLECTIONS_RECORDED,
)
from operator_use.message.types import AgentMessage, TextContent, UserMessage, AssistantMessage
from operator_use.inference.types import (
    LLMContext, LLMEvent, StopReason,
    StartEvent, EndEvent, TextStartEvent, TextDeltaEvent, TextEndEvent,
)
from operator_use.session.manager import SessionManager

CompactionPreparation.model_rebuild(_types_namespace={"AgentMessage": AgentMessage})


# ── Helpers ───────────────────────────────────────────────────────────────────

class FakeLLM:
    def __init__(self, summary: str = "LLM fallback summary."):
        self._summary = summary

    def _events(self, text: str) -> list[LLMEvent]:
        return [
            StartEvent(),
            TextStartEvent(text=TextContent(content="")),
            TextDeltaEvent(text=TextContent(content=text)),
            TextEndEvent(text=TextContent(content=text)),
            EndEvent(reason=StopReason.Stop),
        ]

    async def invoke(self, context: LLMContext, thinking_level=None) -> list[LLMEvent]:
        return self._events(self._summary)

    @property
    def model(self):
        m = MagicMock()
        m.id = "fake-model"
        return m


def make_session(n_user: int = 4, n_assistant: int = 3) -> SessionManager:
    sm = SessionManager.in_memory()
    for i in range(max(n_user, n_assistant)):
        if i < n_user:
            sm.append_message(UserMessage.text(f"user {i}"))
        if i < n_assistant:
            msg = AssistantMessage()
            msg.contents = [TextContent(content=f"assistant {i}")]
            sm.append_message(msg)
    return sm


def make_compaction(obs_config: ObservationPipelineConfig | None = None) -> ObservationalCompaction:
    return ObservationalCompaction(
        llm=FakeLLM(),
        settings=CompactionSettings(enabled=True, reserve_tokens=1000, keep_recent_tokens=1),
        obs_config=obs_config or ObservationPipelineConfig(
            observe_after_tokens=10_000,
            reflect_after_tokens=20_000,
            pool_target_tokens=10_000,
        ),
    )


def inject_obs_ledger(sm: SessionManager, observations: list[Observation]) -> str:
    """Write OM_OBSERVATIONS_RECORDED into the session and return the covers_up_to_id."""
    entries = sm.get_branch()
    covers_id = entries[-1].id if entries else "none"
    sm.append_custom_info(
        OM_OBSERVATIONS_RECORDED,
        {"observations": [o.to_dict() for o in observations], "covers_up_to_id": covers_id},
    )
    return covers_id


def inject_ref_ledger(sm: SessionManager, reflections: list[Reflection]) -> None:
    entries = sm.get_branch()
    covers_id = entries[-1].id if entries else "none"
    sm.append_custom_info(
        OM_REFLECTIONS_RECORDED,
        {"reflections": [r.to_dict() for r in reflections], "covers_up_to_id": covers_id},
    )


# ── Inheritance ───────────────────────────────────────────────────────────────

class TestInheritance:
    def test_should_compact_inherited(self):
        c = make_compaction()
        assert c.should_compact(9500, 10_000) is True
        assert c.should_compact(100, 10_000) is False

    def test_disabled_never_compacts(self):
        c = ObservationalCompaction(
            llm=FakeLLM(),
            settings=CompactionSettings(enabled=False),
        )
        assert c.should_compact(999_999, 1_000_000) is False

    def test_has_obs_pipeline(self):
        c = make_compaction()
        assert isinstance(c.obs_pipeline, ObservationPipeline)

    def test_prepare_inherited(self):
        c = make_compaction()
        sm = make_session()
        prep = c.prepare(sm.get_branch())
        assert prep is not None
        assert prep.retained_from_id


# ── after_turn ────────────────────────────────────────────────────────────────

class TestAfterTurn:
    def test_no_spawn_when_below_threshold(self):
        c = make_compaction(ObservationPipelineConfig(observe_after_tokens=10_000))
        sm = make_session()
        c.after_turn(sm)
        assert c.obs_pipeline.in_flight is False

    def test_spawn_when_threshold_zero(self):
        """With threshold=0 any tokens trigger spawn. Verify spawn is attempted."""
        c = make_compaction(ObservationPipelineConfig(observe_after_tokens=0, enabled=True))
        sm = make_session(n_user=2, n_assistant=1)

        spawned = []
        original_spawn = c.obs_pipeline.spawn_pipeline

        def fake_spawn(session_manager):
            spawned.append(True)

        c.obs_pipeline.spawn_pipeline = fake_spawn
        c.after_turn(sm)
        assert len(spawned) == 1

    def test_no_spawn_when_disabled(self):
        c = make_compaction(ObservationPipelineConfig(observe_after_tokens=0, enabled=False))
        sm = make_session()
        c.after_turn(sm)
        assert c.obs_pipeline.in_flight is False

    def test_no_spawn_when_in_flight(self):
        c = make_compaction(ObservationPipelineConfig(observe_after_tokens=0))
        c.obs_pipeline._in_flight = True
        sm = make_session()
        spawned = []
        c.obs_pipeline.spawn_pipeline = lambda _: spawned.append(True)
        c.after_turn(sm)
        assert spawned == []


# ── compact — ledger path ─────────────────────────────────────────────────────

class TestCompactWithLedger:
    @pytest.mark.asyncio
    async def test_uses_ledger_when_observations_present(self):
        c = make_compaction()
        sm = make_session(n_user=5, n_assistant=4)

        obs = Observation.create("user wants feature X", "high", [])
        inject_obs_ledger(sm, [obs])

        prep = c.prepare(sm.get_branch())
        assert prep is not None

        result = await c.compact(prep)
        assert isinstance(result, CompactionResult)
        assert "feature X" in result.summary
        assert "## Observations" in result.summary

    @pytest.mark.asyncio
    async def test_ledger_with_reflections(self):
        c = make_compaction()
        sm = make_session(n_user=5, n_assistant=4)

        obs = Observation.create("important event", "high", [])
        ref = Reflection.create("user builds in Python", [obs.id])
        inject_obs_ledger(sm, [obs])
        inject_ref_ledger(sm, [ref])

        prep = c.prepare(sm.get_branch())
        assert prep is not None

        result = await c.compact(prep)
        assert "## Reflections" in result.summary
        assert "user builds in Python" in result.summary
        assert "## Observations" in result.summary

    @pytest.mark.asyncio
    async def test_all_ledger_entries_included_regardless_of_boundary(self):
        """Observations are written after the messages they cover, so they appear past
        retained_from_id. compact() folds ALL entries to ensure nothing is missed."""
        c = make_compaction()
        sm = make_session(n_user=5, n_assistant=4)

        # Inject before prepare so _cached_entries includes them
        obs = Observation.create("cross-boundary observation", "high", [])
        inject_obs_ledger(sm, [obs])

        prep = c.prepare(sm.get_branch())
        assert prep is not None

        result = await c.compact(prep)
        # Observation entries live after messages in the branch; they must still appear
        assert "cross-boundary observation" in result.summary


# ── compact — fallback path ───────────────────────────────────────────────────

class TestCompactFallback:
    @pytest.mark.asyncio
    async def test_falls_back_to_llm_when_no_ledger_data(self):
        llm = FakeLLM("LLM fallback summary.")
        c = ObservationalCompaction(
            llm=llm,
            settings=CompactionSettings(enabled=True, keep_recent_tokens=1),
        )
        sm = make_session(n_user=5, n_assistant=4)
        prep = c.prepare(sm.get_branch())
        assert prep is not None

        result = await c.compact(prep)
        assert "LLM fallback summary" in result.summary

    @pytest.mark.asyncio
    async def test_fallback_does_not_contain_observations_header(self):
        c = ObservationalCompaction(
            llm=FakeLLM("plain summary"),
            settings=CompactionSettings(enabled=True, keep_recent_tokens=1),
        )
        sm = make_session(n_user=5, n_assistant=4)
        prep = c.prepare(sm.get_branch())
        assert prep is not None

        result = await c.compact(prep)
        assert "## Observations" not in result.summary
        assert "## Reflections" not in result.summary


# ── /memory obs integration ───────────────────────────────────────────────────

class TestMemoryObsCommand:
    @pytest.mark.asyncio
    async def test_obs_status_no_pipeline(self):
        """When compaction is not ObservationalCompaction, shows disabled message."""
        from operator_use.builtins.commands.memory import _obs_status
        registry = MagicMock()
        session = MagicMock()
        agent = MagicMock()
        agent._compaction = MagicMock(spec=[])  # no obs_pipeline attr
        session.agent = agent
        runtime = MagicMock()
        runtime.current_session = session
        registry.runtime = runtime

        output = []
        with patch("builtins.print", side_effect=output.append):
            await _obs_status(registry)

        assert any("not active" in line for line in output)

    @pytest.mark.asyncio
    async def test_obs_view_no_data(self):
        """When ledger is empty, shows 'nothing recorded yet' message."""
        from operator_use.builtins.commands.memory import _obs_view
        c = make_compaction()
        sm = make_session(n_user=1, n_assistant=1)

        registry = MagicMock()
        session = MagicMock()
        agent = MagicMock()
        agent._compaction = c
        agent._session_manager = sm
        session.agent = agent
        runtime = MagicMock()
        runtime.current_session = session
        registry.runtime = runtime

        output = []
        with patch("builtins.print", side_effect=output.append):
            await _obs_view(registry, full=False)

        assert any("No observational memory" in line for line in output)

    @pytest.mark.asyncio
    async def test_obs_view_with_data(self):
        from operator_use.builtins.commands.memory import _obs_view
        c = make_compaction()
        sm = make_session(n_user=3, n_assistant=2)

        obs = Observation.create("agent wrote tests", "high", [])
        inject_obs_ledger(sm, [obs])

        registry = MagicMock()
        session = MagicMock()
        agent = MagicMock()
        agent._compaction = c
        agent._session_manager = sm
        session.agent = agent
        runtime = MagicMock()
        runtime.current_session = session
        registry.runtime = runtime

        output = []
        with patch("builtins.print", side_effect=output.append):
            await _obs_view(registry, full=False)

        combined = "\n".join(output)
        assert "agent wrote tests" in combined

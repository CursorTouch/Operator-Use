"""Unit tests for the observational memory module."""
from __future__ import annotations

from unittest.mock import MagicMock

from operator_use.compaction.strategy.observational.types import (
    Observation, Reflection,
    OM_OBSERVATIONS_RECORDED, OM_REFLECTIONS_RECORDED, OM_OBSERVATIONS_DROPPED,
)
from operator_use.compaction.strategy.observational.ledger import (
    fold_ledger, render_summary, build_compaction_summary,
)
from operator_use.compaction.strategy.observational.pipeline import (
    ObservationPipeline, ObservationPipelineConfig,
)
from operator_use.compaction.strategy.observational.service import ObservationalCompaction


def _make_custom_entry(custom_type: str, data: dict, entry_id: str = "e1"):
    from operator_use.session.types import CustomInfoEntry
    entry = MagicMock()
    entry.id = entry_id
    entry.custom_type = custom_type
    entry.data = data
    entry.__class__ = CustomInfoEntry
    return entry


def _obs(content: str = "something happened", relevance: str = "medium") -> Observation:
    return Observation.create(content=content, relevance=relevance, source_entry_ids=["src1"])


def _ref(content: str = "user prefers X") -> Reflection:
    return Reflection.create(content=content, supporting_observation_ids=[])


# ── types.py ──────────────────────────────────────────────────────────────────

class TestObservationCreate:
    def test_id_deterministic(self):
        o1 = Observation.create("hello", "medium", ["e1"], timestamp="2024-01-01 10:00")
        o2 = Observation.create("hello", "medium", ["e1"], timestamp="2024-01-01 10:00")
        assert o1.id == o2.id

    def test_id_changes_with_content(self):
        o1 = Observation.create("hello", "medium", [], timestamp="2024-01-01 10:00")
        o2 = Observation.create("world", "medium", [], timestamp="2024-01-01 10:00")
        assert o1.id != o2.id

    def test_token_count(self):
        o = Observation.create("x" * 40, "medium", [])
        assert o.token_count == 10

    def test_roundtrip(self):
        obs = _obs()
        assert Observation.from_dict(obs.to_dict()).id == obs.id
        assert Observation.from_dict(obs.to_dict()).content == obs.content


class TestReflectionCreate:
    def test_roundtrip(self):
        ref = _ref()
        assert Reflection.from_dict(ref.to_dict()).content == ref.content

    def test_supporting_ids(self):
        ref = Reflection.create("fact", ["id1", "id2"])
        assert ref.supporting_observation_ids == ["id1", "id2"]


# ── ledger.py ─────────────────────────────────────────────────────────────────

class TestFoldLedger:
    def test_empty(self):
        fold = fold_ledger([])
        assert fold.active_observations == []
        assert fold.reflections == []

    def test_observations_recorded(self):
        obs = _obs("user asked about X")
        entry = _make_custom_entry(
            OM_OBSERVATIONS_RECORDED,
            {"observations": [obs.to_dict()], "covers_up_to_id": "m10"},
        )
        fold = fold_ledger([entry])
        assert len(fold.active_observations) == 1
        assert fold.latest_observation_coverage_id == "m10"

    def test_dropped_excluded(self):
        obs = _obs("old event")
        record = _make_custom_entry(
            OM_OBSERVATIONS_RECORDED,
            {"observations": [obs.to_dict()], "covers_up_to_id": "m5"},
            entry_id="e1",
        )
        drop = _make_custom_entry(
            OM_OBSERVATIONS_DROPPED,
            {"observation_ids": [obs.id], "covers_up_to_id": "m10"},
            entry_id="e2",
        )
        fold = fold_ledger([record, drop])
        assert fold.active_observations == []

    def test_reflections_recorded(self):
        ref = _ref("user prefers brevity")
        entry = _make_custom_entry(
            OM_REFLECTIONS_RECORDED,
            {"reflections": [ref.to_dict()], "covers_up_to_id": "m20"},
        )
        fold = fold_ledger([entry])
        assert len(fold.reflections) == 1
        assert fold.latest_reflection_coverage_id == "m20"

    def test_malformed_entry_skipped(self):
        entry = _make_custom_entry(
            OM_OBSERVATIONS_RECORDED,
            {"observations": [{"bad": "data"}], "covers_up_to_id": "m5"},
        )
        fold = fold_ledger([entry])
        assert fold.active_observations == []


class TestRenderSummary:
    def test_empty_returns_empty_string(self):
        assert render_summary([], []) == ""

    def test_observations_present(self):
        obs = _obs("tool call succeeded")
        result = render_summary([obs], [])
        assert "## Observations" in result
        assert "tool call succeeded" in result

    def test_reflections_present(self):
        ref = _ref("user works in Python")
        result = render_summary([], [ref])
        assert "## Reflections" in result
        assert "user works in Python" in result

    def test_reflections_before_observations(self):
        result = render_summary([_obs()], [_ref()])
        assert result.index("## Reflections") < result.index("## Observations")


class TestBuildCompactionSummary:
    def test_no_data_returns_none(self):
        assert build_compaction_summary([], "some-id") is None

    def test_returns_summary_when_data_present(self):
        obs = _obs("important event")
        entry = _make_custom_entry(
            OM_OBSERVATIONS_RECORDED,
            {"observations": [obs.to_dict()], "covers_up_to_id": "m5"},
            entry_id="m5",
        )
        result = build_compaction_summary([entry], "m5")
        assert result is not None
        assert "important event" in result

    def test_only_up_to_boundary(self):
        obs_before = _obs("before boundary")
        obs_after = _obs("after boundary")
        e_before = _make_custom_entry(
            OM_OBSERVATIONS_RECORDED,
            {"observations": [obs_before.to_dict()], "covers_up_to_id": "m5"},
            entry_id="m5",
        )
        e_after = _make_custom_entry(
            OM_OBSERVATIONS_RECORDED,
            {"observations": [obs_after.to_dict()], "covers_up_to_id": "m10"},
            entry_id="m10",
        )
        result = build_compaction_summary([e_before, e_after], "m5")
        assert "before boundary" in result
        assert "after boundary" not in result


# ── pipeline.py ───────────────────────────────────────────────────────────────

class TestObservationPipelineShouldRun:
    def _pipeline(self, **kwargs) -> ObservationPipeline:
        return ObservationPipeline(ObservationPipelineConfig(**kwargs), MagicMock())

    def test_disabled_never_runs(self):
        assert self._pipeline(enabled=False).should_run([]) is False

    def test_in_flight_blocks(self):
        p = self._pipeline(observe_after_tokens=0)
        p._in_flight = True
        assert p.should_run([]) is False

    def test_no_tokens_no_run(self):
        assert self._pipeline(observe_after_tokens=1000).should_run([]) is False


# ── ObservationalCompaction ───────────────────────────────────────────────────

class TestObservationalCompaction:
    def test_inherits_should_compact(self):
        from operator_use.compaction.strategy.types import CompactionSettings
        llm = MagicMock()
        llm.model = MagicMock()
        llm.model.id = "test"
        c = ObservationalCompaction(
            llm=llm,
            settings=CompactionSettings(enabled=True, reserve_tokens=1000),
        )
        assert c.should_compact(500, 2000) is False
        assert c.should_compact(1500, 2000) is True

    def test_has_obs_pipeline(self):
        llm = MagicMock()
        llm.model = MagicMock()
        llm.model.id = "test"
        c = ObservationalCompaction(llm=llm)
        assert isinstance(c.obs_pipeline, ObservationPipeline)

    def test_after_turn_no_spawn_when_below_threshold(self):
        from operator_use.compaction.strategy.types import CompactionSettings
        llm = MagicMock()
        session_manager = MagicMock()
        session_manager.get_branch.return_value = []
        c = ObservationalCompaction(
            llm=llm,
            obs_config=ObservationPipelineConfig(observe_after_tokens=10_000),
        )
        c.after_turn(session_manager)
        assert not c.obs_pipeline.in_flight

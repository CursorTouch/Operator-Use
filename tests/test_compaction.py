"""Tests for Compaction: should_compact threshold, prepare, and compact with fake LLM."""
import pytest
from typing import AsyncIterator
from program.compaction.strategy.summarization.service import SummarizationCompaction as Compaction
from program.compaction.strategy.types import CompactionSettings, CompactionResult, CompactionPreparation
from program.message.types import AgentMessage

# CompactionPreparation uses AgentMessage as a TYPE_CHECKING-only forward ref;
# rebuild the model so Pydantic can validate it at runtime.
CompactionPreparation.model_rebuild(_types_namespace={"AgentMessage": AgentMessage})
from program.session.manager import SessionManager
from program.inference.types import (
    LLMContext, LLMEvent, StopReason,
    StartEvent, EndEvent, TextStartEvent, TextDeltaEvent, TextEndEvent,
)
from program.message.types import TextContent, UserMessage, AssistantMessage


# ── Fake LLM for compaction (invoke path) ─────────────────────────────────────

class FakeInvokeLLM:
    """Returns a fixed summary text via invoke()."""
    def __init__(self, summary: str = "Summarized."):
        self._summary = summary

    async def stream(self, context: LLMContext) -> AsyncIterator[LLMEvent]:
        for event in self._text_events(self._summary):
            yield event

    def _text_events(self, text: str) -> list[LLMEvent]:
        return [
            StartEvent(),
            TextStartEvent(text=TextContent(content="")),
            TextDeltaEvent(text=TextContent(content=text)),
            TextEndEvent(text=TextContent(content=text)),
            EndEvent(reason=StopReason.Stop),
        ]

    async def invoke(self, context: LLMContext, thinking_level=None) -> list[LLMEvent]:
        return self._text_events(self._summary)


def make_sm_with_messages(n_user: int = 3, n_assistant: int = 2) -> SessionManager:
    sm = SessionManager.in_memory()
    for i in range(max(n_user, n_assistant)):
        if i < n_user:
            sm.append_message(UserMessage.text(f"user message {i}"))
        if i < n_assistant:
            msg = AssistantMessage()
            msg.contents = [TextContent(content=f"assistant reply {i}")]
            sm.append_message(msg)
    return sm


# ── should_compact ────────────────────────────────────────────────────────────

class TestShouldCompact:
    def test_triggers_when_over_threshold(self):
        settings = CompactionSettings(enabled=True, reserve_tokens=1000)
        c = Compaction(llm=FakeInvokeLLM(), settings=settings)
        assert c.should_compact(context_tokens=9500, context_window=10000)

    def test_no_trigger_when_under_threshold(self):
        settings = CompactionSettings(enabled=True, reserve_tokens=1000)
        c = Compaction(llm=FakeInvokeLLM(), settings=settings)
        assert not c.should_compact(context_tokens=5000, context_window=10000)

    def test_no_trigger_when_disabled(self):
        settings = CompactionSettings(enabled=False, reserve_tokens=100)
        c = Compaction(llm=FakeInvokeLLM(), settings=settings)
        assert not c.should_compact(context_tokens=99999, context_window=100000)

    def test_exactly_at_boundary_triggers(self):
        settings = CompactionSettings(enabled=True, reserve_tokens=500)
        c = Compaction(llm=FakeInvokeLLM(), settings=settings)
        # context_tokens (9501) > context_window (10000) - reserve (500) = 9500
        assert c.should_compact(context_tokens=9501, context_window=10000)

    def test_exactly_at_boundary_no_trigger(self):
        settings = CompactionSettings(enabled=True, reserve_tokens=500)
        c = Compaction(llm=FakeInvokeLLM(), settings=settings)
        assert not c.should_compact(context_tokens=9500, context_window=10000)


# ── prepare ───────────────────────────────────────────────────────────────────

class TestPrepare:
    def test_prepare_returns_none_for_empty_session(self):
        c = Compaction(llm=FakeInvokeLLM())
        assert c.prepare([]) is None

    def test_prepare_returns_none_if_last_entry_is_compaction(self):
        sm = make_sm_with_messages()
        first_entry_id = sm.get_entries()[0].id
        sm.append_compaction("prior summary", first_entry_id, 1000)
        path = sm.get_branch()
        c = Compaction(llm=FakeInvokeLLM())
        assert c.prepare(path) is None

    def test_prepare_returns_preparation_with_messages(self):
        sm = make_sm_with_messages(n_user=4, n_assistant=3)
        path = sm.get_branch()
        settings = CompactionSettings(enabled=True, keep_recent_tokens=1)
        c = Compaction(llm=FakeInvokeLLM(), settings=settings)
        prep = c.prepare(path)
        # With keep_recent_tokens=1, most messages should be summarized
        assert prep is not None
        assert prep.retained_from_id

    def test_prepare_returns_retained_from_id_exists_in_session(self):
        sm = make_sm_with_messages(n_user=5, n_assistant=4)
        path = sm.get_branch()
        c = Compaction(llm=FakeInvokeLLM(), settings=CompactionSettings(keep_recent_tokens=1))
        prep = c.prepare(path)
        if prep is not None:
            assert prep.retained_from_id in sm.by_id

    def test_tokens_before_positive(self):
        sm = make_sm_with_messages(n_user=4, n_assistant=3)
        path = sm.get_branch()
        c = Compaction(llm=FakeInvokeLLM(), settings=CompactionSettings(keep_recent_tokens=1))
        prep = c.prepare(path)
        if prep is not None:
            assert prep.tokens_before > 0


# ── compact ───────────────────────────────────────────────────────────────────

class TestCompact:
    @pytest.mark.asyncio
    async def test_compact_returns_result(self):
        sm = make_sm_with_messages(n_user=5, n_assistant=4)
        path = sm.get_branch()
        llm = FakeInvokeLLM(summary="All previous work summarized here.")
        c = Compaction(llm=llm, settings=CompactionSettings(keep_recent_tokens=1))
        prep = c.prepare(path)
        if prep is None:
            pytest.skip("prepare returned None — not enough entries to compact")

        result = await c.compact(prep)
        assert isinstance(result, CompactionResult)
        assert result.summary
        assert "All previous work summarized here." in result.summary
        assert result.retained_from_id == prep.retained_from_id

    @pytest.mark.asyncio
    async def test_compact_result_has_tokens_before(self):
        sm = make_sm_with_messages(n_user=5, n_assistant=4)
        path = sm.get_branch()
        c = Compaction(llm=FakeInvokeLLM(), settings=CompactionSettings(keep_recent_tokens=1))
        prep = c.prepare(path)
        if prep is None:
            pytest.skip("not enough entries")

        result = await c.compact(prep)
        assert result.tokens_before == prep.tokens_before

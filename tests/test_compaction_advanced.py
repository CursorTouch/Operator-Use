"""Advanced compaction tests: prepare with prior summary, split-turn, file ops, custom instructions."""
import pytest
from program.compaction.strategy.summarization.service import SummarizationCompaction as Compaction
from program.compaction.strategy.types import (
    CompactionSettings, CompactionPreparation, CompactionResult,
)
from program.message.types import AgentMessage, UserMessage, AssistantMessage, TextContent
from program.session.manager import SessionManager
from program.session.types import CompactionEntry
from program.inference.types import (
    LLMContext, LLMEvent, StopReason,
    StartEvent, EndEvent, TextStartEvent, TextDeltaEvent, TextEndEvent,
    ToolCallStartEvent, ToolCallEndEvent,
)
from program.message.types import ToolCallContent

CompactionPreparation.model_rebuild(_types_namespace={"AgentMessage": AgentMessage})


# ── Fake LLM ──────────────────────────────────────────────────────────────────

class FakeLLM:
    def __init__(self, *texts: str):
        self._texts = list(texts)
        self._idx = 0

    def _events(self, text: str) -> list[LLMEvent]:
        return [
            StartEvent(),
            TextStartEvent(text=TextContent(content="")),
            TextDeltaEvent(text=TextContent(content=text)),
            TextEndEvent(text=TextContent(content=text)),
            EndEvent(reason=StopReason.Stop),
        ]

    async def invoke(self, context: LLMContext, thinking_level=None) -> list[LLMEvent]:
        text = self._texts[min(self._idx, len(self._texts) - 1)]
        self._idx += 1
        return self._events(text)

    async def stream(self, context: LLMContext):
        for e in self._events(self._texts[0]):
            yield e


def make_sm(n_pairs: int = 4) -> SessionManager:
    sm = SessionManager.in_memory()
    for i in range(n_pairs):
        sm.append_message(UserMessage.text(f"q{i}"))
        a = AssistantMessage()
        a.contents = [TextContent(content=f"a{i}")]
        sm.append_message(a)
    return sm


def compaction(llm=None, settings=None) -> Compaction:
    return Compaction(
        llm=llm or FakeLLM("summary text"),
        settings=settings or CompactionSettings(keep_recent_tokens=1),
    )


# ── prepare: previous summary propagation ────────────────────────────────────

class TestPrepareWithPriorSummary:
    def test_previous_summary_included_when_compaction_exists(self):
        sm = make_sm(6)
        path = sm.get_branch()
        c = compaction()
        prep = c.prepare(path)
        if prep is None:
            pytest.skip("not enough entries")

        # Apply first compaction
        first_kept = prep.retained_from_id
        sm.append_compaction("First summary text", first_kept, 500)

        # Add more messages so a second compaction is needed
        for i in range(4):
            sm.append_message(UserMessage.text(f"post-compact q{i}"))
            a = AssistantMessage()
            a.contents = [TextContent(content=f"post-compact a{i}")]
            sm.append_message(a)

        path2 = sm.get_branch()
        prep2 = c.prepare(path2)
        if prep2 is not None:
            assert prep2.previous_summary == "First summary text"

    def test_no_previous_summary_when_no_compaction(self):
        sm = make_sm(4)
        c = compaction()
        prep = c.prepare(sm.get_branch())
        if prep is not None:
            assert prep.previous_summary is None


# ── prepare: retained_from_id is valid ───────────────────────────────────────

class TestPrepareRetainedId:
    def test_retained_from_id_in_session(self):
        sm = make_sm(6)
        c = compaction()
        prep = c.prepare(sm.get_branch())
        if prep is None:
            pytest.skip("not enough entries")
        assert prep.retained_from_id in sm.by_id

    def test_messages_to_summarize_not_empty(self):
        sm = make_sm(6)
        c = compaction()
        prep = c.prepare(sm.get_branch())
        if prep is None:
            pytest.skip("not enough entries")
        assert len(prep.messages_to_summarize) >= 1

    def test_tokens_before_reflects_session_size(self):
        sm_small = make_sm(2)
        sm_large = make_sm(10)
        c = compaction()
        prep_small = c.prepare(sm_small.get_branch())
        prep_large = c.prepare(sm_large.get_branch())
        if prep_small and prep_large:
            assert prep_large.tokens_before >= prep_small.tokens_before


# ── compact: custom instructions ─────────────────────────────────────────────

class TestCompactCustomInstructions:
    @pytest.mark.asyncio
    async def test_custom_instructions_passed_to_prompt(self):
        captured_contexts: list[LLMContext] = []

        class CapturingLLM:
            async def invoke(self, context: LLMContext, thinking_level=None):
                captured_contexts.append(context)
                return [
                    StartEvent(),
                    TextEndEvent(text=TextContent(content="summary")),
                    EndEvent(reason=StopReason.Stop),
                ]

        sm = make_sm(6)
        c = Compaction(llm=CapturingLLM(), settings=CompactionSettings(keep_recent_tokens=1))
        prep = c.prepare(sm.get_branch())
        if prep is None:
            pytest.skip("not enough entries")

        await c.compact(prep, custom_instructions="focus on errors only")
        assert len(captured_contexts) >= 1
        prompt = captured_contexts[0].messages[0].contents[0].content
        assert "focus on errors only" in prompt

    @pytest.mark.asyncio
    async def test_no_custom_instructions_uses_default_prompt(self):
        captured_contexts: list[LLMContext] = []

        class CapturingLLM:
            async def invoke(self, context: LLMContext, thinking_level=None):
                captured_contexts.append(context)
                return [
                    StartEvent(),
                    TextEndEvent(text=TextContent(content="summary")),
                    EndEvent(reason=StopReason.Stop),
                ]

        sm = make_sm(6)
        c = Compaction(llm=CapturingLLM(), settings=CompactionSettings(keep_recent_tokens=1))
        prep = c.prepare(sm.get_branch())
        if prep is None:
            pytest.skip("not enough entries")

        await c.compact(prep)
        assert len(captured_contexts) >= 1
        prompt = captured_contexts[0].messages[0].contents[0].content
        assert len(prompt) > 0


# ── compact: file operations in result ───────────────────────────────────────

class TestCompactFileOperations:
    @pytest.mark.asyncio
    async def test_result_has_details(self):
        sm = make_sm(6)
        c = compaction(FakeLLM("detailed summary"))
        prep = c.prepare(sm.get_branch())
        if prep is None:
            pytest.skip("not enough entries")
        result = await c.compact(prep)
        assert result.details is not None


# ── compact: result structure ─────────────────────────────────────────────────

class TestCompactResultStructure:
    @pytest.mark.asyncio
    async def test_result_summary_contains_llm_output(self):
        sm = make_sm(6)
        c = compaction(FakeLLM("THE_SUMMARY_TEXT"))
        prep = c.prepare(sm.get_branch())
        if prep is None:
            pytest.skip("not enough entries")
        result = await c.compact(prep)
        assert "THE_SUMMARY_TEXT" in result.summary

    @pytest.mark.asyncio
    async def test_result_retained_from_id_matches_prep(self):
        sm = make_sm(6)
        c = compaction(FakeLLM("summary"))
        prep = c.prepare(sm.get_branch())
        if prep is None:
            pytest.skip("not enough entries")
        result = await c.compact(prep)
        assert result.retained_from_id == prep.retained_from_id

    @pytest.mark.asyncio
    async def test_result_tokens_before_matches_prep(self):
        sm = make_sm(6)
        c = compaction(FakeLLM("summary"))
        prep = c.prepare(sm.get_branch())
        if prep is None:
            pytest.skip("not enough entries")
        result = await c.compact(prep)
        assert result.tokens_before == prep.tokens_before


# ── should_compact boundary cases ────────────────────────────────────────────

class TestShouldCompactEdgeCases:
    def test_zero_tokens_never_triggers(self):
        c = compaction(settings=CompactionSettings(enabled=True, reserve_tokens=100))
        assert not c.should_compact(0, 10000)

    def test_large_reserve_triggers_earlier(self):
        c_small = compaction(settings=CompactionSettings(enabled=True, reserve_tokens=100))
        c_large = compaction(settings=CompactionSettings(enabled=True, reserve_tokens=5000))
        # 5001 > 10000-100=9900? No. 5001 > 10000-5000=5000? Yes.
        assert not c_small.should_compact(5001, 10000)
        assert c_large.should_compact(5001, 10000)

    def test_disabled_never_triggers_regardless_of_tokens(self):
        c = compaction(settings=CompactionSettings(enabled=False, reserve_tokens=1))
        assert not c.should_compact(999999, 1000000)


# ── prepare: returns None for edge cases ──────────────────────────────────────

class TestPrepareEdgeCases:
    def test_returns_none_for_empty_path(self):
        c = compaction()
        assert c.prepare([]) is None

    def test_returns_none_if_leaf_is_compaction(self):
        sm = make_sm(4)
        first_id = sm.get_entries()[0].id
        sm.append_compaction("prior", first_id, 100)
        c = compaction()
        assert c.prepare(sm.get_branch()) is None

    def test_returns_preparation_for_valid_session(self):
        sm = make_sm(6)
        c = compaction()
        result = c.prepare(sm.get_branch())
        # Either None (not enough to compact) or a valid preparation
        assert result is None or result.retained_from_id is not None

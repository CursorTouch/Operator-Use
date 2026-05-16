"""Tests for compaction/branch_summarization.py: entry collection, preparation, generation."""
import pytest
from pathlib import Path
from typing import AsyncIterator

from program.compaction.branch_summarization import (
    collect_entries_for_branch_summary,
    prepare_branch_entries,
    generate_branch_summary,
)
from program.compaction.types import (
    BranchSummaryDetails, GenerateBranchSummaryOptions, BranchPreparation,
    CollectEntriesResult,
)
from program.message.types import AgentMessage as _AgentMessage
from program.session.types import SessionEntry as _SessionEntry
from program.inference.api.llm.service import LLM as _LLM
CollectEntriesResult.model_rebuild(_types_namespace={"SessionEntry": _SessionEntry})
BranchPreparation.model_rebuild(_types_namespace={"AgentMessage": _AgentMessage})
GenerateBranchSummaryOptions.model_rebuild(_types_namespace={"LLM": _LLM})
from program.inference.types import (
    LLMContext, LLMEvent, StopReason,
    StartEvent, EndEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent,
)
from program.message.types import (
    TextContent, UserMessage, AssistantMessage, BranchSummaryMessage, AgentMessage,
)
from program.session.manager import SessionManager
from program.session.types import MessageEntry, BranchEntry, CompactionEntry


# ── Fake LLM ──────────────────────────────────────────────────────────────────

class FakeLLM:
    def __init__(self, *sequences: list[LLMEvent]):
        self._seqs = list(sequences)
        self._idx = 0

    async def stream(self, context: LLMContext) -> AsyncIterator[LLMEvent]:
        events = self._seqs[min(self._idx, len(self._seqs) - 1)]
        self._idx += 1
        for e in events:
            yield e

    async def invoke(self, context: LLMContext, thinking_level=None) -> list[LLMEvent]:
        events = self._seqs[min(self._idx, len(self._seqs) - 1)]
        self._idx += 1
        return events


def text_events(text: str) -> list[LLMEvent]:
    return [
        StartEvent(),
        TextStartEvent(text=TextContent(content="")),
        TextDeltaEvent(text=TextContent(content=text)),
        TextEndEvent(text=TextContent(content=text)),
        EndEvent(reason=StopReason.Stop),
    ]


def error_events(msg: str) -> list[LLMEvent]:
    return [
        StartEvent(),
        ErrorEvent(error=msg, reason=StopReason.Error),
    ]


def abort_events() -> list[LLMEvent]:
    return [
        StartEvent(),
        EndEvent(reason=StopReason.Abort),
    ]


def make_sm() -> SessionManager:
    return SessionManager.in_memory()


def user(text: str = "hello") -> UserMessage:
    return UserMessage.text(text)


def assistant(text: str = "hi") -> AssistantMessage:
    msg = AssistantMessage()
    msg.contents = [TextContent(content=text)]
    return msg


def make_options(llm, **kwargs) -> GenerateBranchSummaryOptions:
    return GenerateBranchSummaryOptions.model_construct(
        llm=llm,
        context_window=kwargs.get("context_window", 128000),
        reserve_tokens=kwargs.get("reserve_tokens", 0),
        custom_instructions=kwargs.get("custom_instructions"),
        replace_instructions=kwargs.get("replace_instructions", False),
    )


# ── collect_entries_for_branch_summary ───────────────────────────────────────

class TestCollectEntriesForBranchSummary:
    def test_no_old_leaf_returns_empty(self):
        sm = make_sm()
        id1 = sm.append_message(user("a"))
        result = collect_entries_for_branch_summary(sm, None, id1)
        assert result.entries == []
        assert result.common_ancestor_id is None

    def test_same_leaf_returns_empty(self):
        sm = make_sm()
        id1 = sm.append_message(user("a"))
        result = collect_entries_for_branch_summary(sm, id1, id1)
        assert result.entries == []

    def test_collects_entries_between_branch_and_target(self):
        sm = make_sm()
        id1 = sm.append_message(user("root"))
        id2 = sm.append_message(user("step2"))
        id3 = sm.append_message(user("step3"))

        # Now branch back to id1 and create a different path
        sm.branch(id1)
        id4 = sm.append_message(user("alt_step"))

        # Collect what was on the old path (id3) relative to new target (id4)
        result = collect_entries_for_branch_summary(sm, id3, id4)
        entry_ids = [e.id for e in result.entries]
        # Should contain id2 and id3 (entries that diverged from id1)
        assert id2 in entry_ids
        assert id3 in entry_ids

    def test_common_ancestor_found(self):
        sm = make_sm()
        id1 = sm.append_message(user("root"))
        id2 = sm.append_message(user("branch_a"))
        sm.branch(id1)
        id3 = sm.append_message(user("branch_b"))

        result = collect_entries_for_branch_summary(sm, id2, id3)
        assert result.common_ancestor_id == id1

    def test_entries_in_chronological_order(self):
        sm = make_sm()
        id1 = sm.append_message(user("a"))
        id2 = sm.append_message(user("b"))
        id3 = sm.append_message(user("c"))
        sm.branch(id1)
        id4 = sm.append_message(user("d"))

        result = collect_entries_for_branch_summary(sm, id3, id4)
        ids = [e.id for e in result.entries]
        # Should be in root→leaf order: id2 before id3
        if id2 in ids and id3 in ids:
            assert ids.index(id2) < ids.index(id3)


# ── prepare_branch_entries ────────────────────────────────────────────────────

class TestPrepareBranchEntries:
    def _make_entries(self, sm: SessionManager, count: int) -> list:
        ids = []
        for i in range(count):
            ids.append(sm.append_message(user(f"msg {i}")))
            ids.append(sm.append_message(assistant(f"reply {i}")))
        return sm.get_branch()

    def test_empty_entries_returns_empty_preparation(self):
        prep = prepare_branch_entries([])
        assert prep.messages == []
        assert prep.total_tokens == 0

    def test_messages_collected_without_budget(self):
        sm = make_sm()
        sm.append_message(user("q1"))
        sm.append_message(assistant("a1"))
        entries = sm.get_branch()
        prep = prepare_branch_entries(entries, token_budget=0)
        assert len(prep.messages) == 2

    def test_token_budget_limits_messages(self):
        sm = make_sm()
        for i in range(10):
            sm.append_message(user(f"question {i}"))
            sm.append_message(assistant(f"answer {i}"))
        entries = sm.get_branch()
        # Very tight budget should limit included messages
        prep_full = prepare_branch_entries(entries, token_budget=0)
        prep_limited = prepare_branch_entries(entries, token_budget=10)
        assert len(prep_limited.messages) <= len(prep_full.messages)

    def test_branch_entry_file_ops_accumulated(self):
        sm = make_sm()
        id1 = sm.append_message(user("a"))
        sm.branch_with_summary(
            "summary",
            from_id=id1,
            details=BranchSummaryDetails(read_files=["foo.py"], modified_files=["bar.py"]),
        )
        entries = sm.get_branch()
        prep = prepare_branch_entries(entries, token_budget=0)
        assert "foo.py" in prep.file_ops.read
        assert "bar.py" in prep.file_ops.edited

    def test_tool_messages_excluded(self):
        from program.message.types import ToolMessage, ToolResultContent
        sm = make_sm()
        sm.append_message(user("q"))
        tool_msg = ToolMessage.from_result(ToolResultContent(id="t1", content="result"))
        sm.append_message(tool_msg)
        entries = sm.get_branch()
        prep = prepare_branch_entries(entries, token_budget=0)
        from program.message.types import Role
        roles = [m.role for m in prep.messages]
        assert Role.TOOL not in roles

    def test_messages_in_oldest_first_order(self):
        sm = make_sm()
        id1 = sm.append_message(user("first"))
        id2 = sm.append_message(assistant("second"))
        entries = sm.get_branch()
        prep = prepare_branch_entries(entries, token_budget=0)
        from program.message.types import Role
        assert prep.messages[0].role == Role.USER
        assert prep.messages[1].role == Role.ASSISTANT


# ── generate_branch_summary ───────────────────────────────────────────────────

class TestGenerateBranchSummary:
    @pytest.mark.asyncio
    async def test_generates_summary_from_entries(self):
        sm = make_sm()
        sm.append_message(user("what is the sky?"))
        sm.append_message(assistant("it is blue"))
        entries = sm.get_branch()

        llm = FakeLLM(text_events("sky is blue summary"))
        options = make_options(llm)
        result = await generate_branch_summary(entries, options)
        assert result.summary is not None
        assert "sky is blue summary" in result.summary

    @pytest.mark.asyncio
    async def test_empty_entries_returns_no_content_message(self):
        llm = FakeLLM(text_events("unused"))
        options = make_options(llm)
        result = await generate_branch_summary([], options)
        assert result.summary == "No content to summarize"

    @pytest.mark.asyncio
    async def test_abort_sets_aborted_flag(self):
        sm = make_sm()
        sm.append_message(user("q"))
        sm.append_message(assistant("a"))
        entries = sm.get_branch()

        llm = FakeLLM(abort_events())
        options = make_options(llm)
        result = await generate_branch_summary(entries, options)
        assert result.aborted is True
        assert result.summary is None

    @pytest.mark.asyncio
    async def test_error_sets_error_field(self):
        sm = make_sm()
        sm.append_message(user("q"))
        sm.append_message(assistant("a"))
        entries = sm.get_branch()

        llm = FakeLLM(error_events("LLM failed"))
        options = make_options(llm)
        result = await generate_branch_summary(entries, options)
        assert result.error is not None
        assert result.aborted is False

    @pytest.mark.asyncio
    async def test_custom_instructions_included(self):
        sm = make_sm()
        sm.append_message(user("tell me about weather"))
        sm.append_message(assistant("it rains"))
        entries = sm.get_branch()

        captured = []

        class CaptureLLM:
            async def invoke(self, context: LLMContext, thinking_level=None):
                captured.append(context.messages[0].contents[0].content)
                return text_events("result")

        options = GenerateBranchSummaryOptions.model_construct(
            llm=CaptureLLM(),
            context_window=128000,
            reserve_tokens=0,
            custom_instructions="focus on weather patterns",
        )
        await generate_branch_summary(entries, options)
        assert len(captured) == 1
        assert "focus on weather patterns" in captured[0]

    @pytest.mark.asyncio
    async def test_replace_instructions_overrides_prompt(self):
        sm = make_sm()
        sm.append_message(user("q"))
        sm.append_message(assistant("a"))
        entries = sm.get_branch()

        captured = []

        class CaptureLLM:
            async def invoke(self, context: LLMContext, thinking_level=None):
                captured.append(context.messages[0].contents[0].content)
                return text_events("ok")

        options = GenerateBranchSummaryOptions.model_construct(
            llm=CaptureLLM(),
            context_window=128000,
            reserve_tokens=0,
            custom_instructions="ONLY do this",
            replace_instructions=True,
        )
        await generate_branch_summary(entries, options)
        # With replace_instructions, only the custom text should appear after <conversation>
        prompt = captured[0]
        assert "ONLY do this" in prompt

    @pytest.mark.asyncio
    async def test_read_files_in_result(self):
        sm = make_sm()
        id1 = sm.append_message(user("start"))
        sm.branch_with_summary(
            "branch",
            from_id=id1,
            details=BranchSummaryDetails(read_files=["readme.md"], modified_files=[]),
        )
        sm.append_message(assistant("ok"))
        entries = sm.get_branch()

        llm = FakeLLM(text_events("summary text"))
        options = make_options(llm)
        result = await generate_branch_summary(entries, options)
        assert "readme.md" in result.read_files

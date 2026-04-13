"""Tests for context management strategies and ContextPlugin."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from operator_use.context import BaseContextStrategy, ContextPlugin
from operator_use.context.strategies import (
    CompactionStrategy,
    SlidingWindowStrategy,
    ObservationMaskingStrategy,
)
from operator_use.messages import SystemMessage, HumanMessage, ToolMessage, AIMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sys(content="System prompt"):
    return SystemMessage(content=content)


def human(content="user message"):
    return HumanMessage(content=content)


def ai(content="assistant response"):
    return AIMessage(content=content)


def tool(content="tool result", name="browser"):
    return ToolMessage(id="t1", name=name, params={}, content=content)


def make_llm(summary="summary text"):
    from operator_use.providers.events import LLMEvent, LLMEventType

    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=LLMEvent(type=LLMEventType.TEXT, content=summary))
    return llm


# ---------------------------------------------------------------------------
# BaseContextStrategy — interface
# ---------------------------------------------------------------------------


def test_base_strategy_is_abstract():
    with pytest.raises(TypeError):
        BaseContextStrategy()


# ---------------------------------------------------------------------------
# SlidingWindowStrategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sliding_window_below_threshold_unchanged():
    msgs = [sys(), human("a"), ai("b"), human("c")]
    result = await SlidingWindowStrategy(window=10).process(msgs)
    assert result == msgs


@pytest.mark.asyncio
async def test_sliding_window_trims_oldest_non_system():
    s = sys()
    msgs = [s, human("1"), ai("2"), human("3"), ai("4"), human("5")]
    result = await SlidingWindowStrategy(window=3).process(msgs)
    assert result[0] is s  # system message preserved
    assert len(result) == 4  # system + 3 non-system
    assert result[-1].content == "5"  # most recent kept


@pytest.mark.asyncio
async def test_sliding_window_preserves_all_system_messages():
    s1, s2 = sys("sys1"), sys("sys2")
    msgs = [s1, s2, human("a"), human("b"), human("c"), human("d")]
    result = await SlidingWindowStrategy(window=2).process(msgs)
    assert result[0] is s1
    assert result[1] is s2
    assert len(result) == 4  # 2 system + 2 recent


@pytest.mark.asyncio
async def test_sliding_window_exactly_at_threshold_unchanged():
    msgs = [sys(), human("a"), ai("b"), human("c")]  # 3 non-system
    result = await SlidingWindowStrategy(window=3).process(msgs)
    assert result == msgs


@pytest.mark.asyncio
async def test_sliding_window_does_not_need_llm():
    msgs = [sys()] + [human(str(i)) for i in range(25)]
    result = await SlidingWindowStrategy(window=10).process(msgs, llm=None)
    assert len(result) == 11  # system + 10 most recent


# ---------------------------------------------------------------------------
# CompactionStrategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_below_threshold_unchanged():
    msgs = [sys(), human("a"), ai("b")]
    result = await CompactionStrategy(threshold=10).process(msgs, llm=make_llm())
    assert result == msgs


@pytest.mark.asyncio
async def test_compaction_summarises_when_threshold_reached():
    s = sys()
    msgs = [s] + [human(str(i)) for i in range(5)]
    llm = make_llm("concise summary")
    result = await CompactionStrategy(threshold=5).process(msgs, llm=llm)

    assert result[0] is s
    assert len(result) == 2  # system + summary
    assert "[Conversation history compacted]" in result[1].content
    assert "concise summary" in result[1].content
    llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_compaction_skips_when_no_llm():
    msgs = [sys()] + [human(str(i)) for i in range(10)]
    result = await CompactionStrategy(threshold=5).process(msgs, llm=None)
    assert result == msgs  # unchanged — can't compact without LLM


@pytest.mark.asyncio
async def test_compaction_skips_when_llm_returns_no_text():
    from operator_use.providers.events import LLMEvent, LLMEventType

    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=LLMEvent(type=LLMEventType.TEXT, content=""))
    msgs = [sys()] + [human(str(i)) for i in range(10)]
    result = await CompactionStrategy(threshold=5).process(msgs, llm=llm)
    assert result == msgs  # unchanged — empty response treated as failure


@pytest.mark.asyncio
async def test_compaction_skips_on_llm_exception():
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("network error"))
    msgs = [sys()] + [human(str(i)) for i in range(10)]
    result = await CompactionStrategy(threshold=5).process(msgs, llm=llm)
    assert result == msgs  # graceful fallback


@pytest.mark.asyncio
async def test_compaction_preserves_multiple_system_messages():
    s1, s2 = sys("sys1"), sys("sys2")
    msgs = [s1, s2] + [human(str(i)) for i in range(5)]
    result = await CompactionStrategy(threshold=5).process(msgs, llm=make_llm("summary"))
    assert result[0] is s1
    assert result[1] is s2
    assert len(result) == 3  # 2 system + 1 summary


# ---------------------------------------------------------------------------
# ObservationMaskingStrategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observation_masking_below_threshold_unchanged():
    msgs = [sys(), human("q"), tool("result"), ai("a")]
    result = await ObservationMaskingStrategy(keep_recent=10).process(msgs)
    assert result == msgs


@pytest.mark.asyncio
async def test_observation_masking_blanks_old_tool_messages():
    s = sys()
    old_tool = tool("big scrape result")
    recent = [human("recent q"), ai("recent a")]
    msgs = [s, old_tool] + recent

    result = await ObservationMaskingStrategy(keep_recent=2).process(msgs)

    assert result[0] is s
    assert result[1].content == "[masked — call the tool again if needed]"
    assert result[2] is recent[0]
    assert result[3] is recent[1]


@pytest.mark.asyncio
async def test_observation_masking_leaves_non_tool_messages_intact():
    s = sys()
    old_human = human("old question")
    old_ai = ai("old answer")
    recent = [human("new q"), ai("new a")]
    msgs = [s, old_human, old_ai] + recent

    result = await ObservationMaskingStrategy(keep_recent=2).process(msgs)

    # non-tool old messages are NOT masked
    assert result[1].content == "old question"
    assert result[2].content == "old answer"
    assert result[3] is recent[0]
    assert result[4] is recent[1]


@pytest.mark.asyncio
async def test_observation_masking_does_not_mutate_original_message():
    s = sys()
    original_tool = tool("important data")
    msgs = [s, original_tool, human("1"), ai("2"), human("3")]

    await ObservationMaskingStrategy(keep_recent=2).process(msgs)

    # original object must be untouched (copy was made)
    assert original_tool.content == "important data"


@pytest.mark.asyncio
async def test_observation_masking_already_masked_not_remasked():
    s = sys()
    already_masked = tool("[masked — call the tool again if needed]")
    msgs = [s, already_masked, human("1"), ai("2"), human("3")]

    result = await ObservationMaskingStrategy(keep_recent=2).process(msgs)

    # should still be the same object (no copy made)
    assert result[1] is already_masked


@pytest.mark.asyncio
async def test_observation_masking_keeps_recent_count_exact():
    s = sys()
    msgs = [s] + [human(str(i)) for i in range(10)]
    result = await ObservationMaskingStrategy(keep_recent=4).process(msgs)

    non_system = [m for m in result if not isinstance(m, SystemMessage)]
    recent = non_system[-4:]

    assert [m.content for m in recent] == ["6", "7", "8", "9"]
    assert all(m.content != "[masked — call the tool again if needed]" for m in recent)


@pytest.mark.asyncio
async def test_observation_masking_does_not_need_llm():
    msgs = [sys()] + [tool(f"result {i}") for i in range(15)]
    result = await ObservationMaskingStrategy(keep_recent=5).process(msgs, llm=None)
    assert len(result) == 16  # all messages kept, just some masked


# ---------------------------------------------------------------------------
# ContextPlugin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_plugin_calls_strategy_on_hook():
    from operator_use.agent.hooks.events import BeforeLLMCallContext

    called_with = {}

    class FakeStrategy(BaseContextStrategy):
        async def process(self, messages, llm=None):
            called_with["messages"] = messages
            called_with["llm"] = llm
            return messages[:1]  # return just the first message

    llm = MagicMock()
    plugin = ContextPlugin(strategy=FakeStrategy(), llm=llm)

    msgs = [sys(), human("hello"), ai("hi")]
    ctx = BeforeLLMCallContext(session=None, messages=msgs, iteration=0)
    await plugin._hook(ctx)

    assert called_with["messages"] == msgs
    assert called_with["llm"] is llm
    assert ctx.messages == msgs[:1]  # strategy result applied


@pytest.mark.asyncio
async def test_context_plugin_registers_and_unregisters_hook():
    from operator_use.agent.hooks import Hooks, HookEvent

    hooks = Hooks()
    plugin = ContextPlugin(strategy=SlidingWindowStrategy(window=5))
    plugin.register_hooks(hooks)
    assert len(hooks._handlers[HookEvent.BEFORE_LLM_CALL]) == 1

    plugin.unregister_hooks(hooks)
    assert len(hooks._handlers[HookEvent.BEFORE_LLM_CALL]) == 0


@pytest.mark.asyncio
async def test_context_plugin_integrates_with_sliding_window():
    from operator_use.agent.hooks import Hooks, HookEvent
    from operator_use.agent.hooks.events import BeforeLLMCallContext

    hooks = Hooks()
    plugin = ContextPlugin(strategy=SlidingWindowStrategy(window=3))
    plugin.register_hooks(hooks)

    msgs = [sys()] + [human(str(i)) for i in range(6)]
    ctx = BeforeLLMCallContext(session=None, messages=msgs, iteration=0)
    await hooks.emit(HookEvent.BEFORE_LLM_CALL, ctx)

    assert len(ctx.messages) == 4  # system + 3 recent
    assert ctx.messages[-1].content == "5"

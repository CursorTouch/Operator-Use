"""Engine event emission — every hook event type, turn lifecycle, message lifecycle."""
from __future__ import annotations

import pytest
from helpers import FakeLLM, make_tool, text_seq, tool_call_seq, collect_events, AnyParams

from program.engine.service import Engine
from program.engine.types import (
    AgentStartEvent, AgentEndEvent, AgentErrorEvent,
    TurnStartEvent, TurnEndEvent,
    MessageStartEvent, MessageEndEvent, MessageUpdateEvent,
    ToolExecutionStartEvent, ToolExecutionEndEvent,
    Options,
)
from program.hooks.service import Hooks
from program.hooks.types import (
    AgentStartEvent as HookAgentStart, AgentEndEvent as HookAgentEnd,
    TurnStartEvent as HookTurnStart, TurnEndEvent as HookTurnEnd,
    MessageStartEvent as HookMsgStart, MessageEndEvent as HookMsgEnd,
    ToolExecutionStartEvent as HookToolStart, ToolExecutionEndEvent as HookToolEnd,
    SavePointEvent, SettledEvent,
)
from program.message.types import UserMessage, Role
from program.session.manager import SessionManager


class TestEngineEventSequence:
    @pytest.mark.asyncio
    async def test_agent_start_and_end_fired(self):
        engine = Engine(llm=FakeLLM(text_seq()), tools=[])
        events = await collect_events(engine)
        assert any(isinstance(e, AgentStartEvent) for e in events)
        assert any(isinstance(e, AgentEndEvent) for e in events)
        assert events[0].__class__ == AgentStartEvent
        assert events[-1].__class__ == AgentEndEvent

    @pytest.mark.asyncio
    async def test_turn_start_end_fired_each_turn(self):
        engine = Engine(llm=FakeLLM(tool_call_seq("t1", "t"), text_seq()), tools=[make_tool("t")])
        events = await collect_events(engine)
        starts = [e for e in events if isinstance(e, TurnStartEvent)]
        ends = [e for e in events if isinstance(e, TurnEndEvent)]
        assert len(starts) == 2
        assert len(ends) == 2

    @pytest.mark.asyncio
    async def test_message_start_update_end_sequence(self):
        captured = []
        async def on_ev(e): captured.append(e)
        engine = Engine(llm=FakeLLM(text_seq("hello")), tools=[],
                        options=Options(on_event=on_ev))
        await engine.run([UserMessage.text("go")])
        types = [type(e).__name__ for e in captured]
        assert "MessageStartEvent" in types
        assert "MessageUpdateEvent" in types
        assert "MessageEndEvent" in types

    @pytest.mark.asyncio
    async def test_tool_exec_start_end_fired(self):
        engine = Engine(llm=FakeLLM(tool_call_seq("t1", "my_tool"), text_seq()), tools=[make_tool("my_tool")])
        events = await collect_events(engine)
        assert any(isinstance(e, ToolExecutionStartEvent) for e in events)
        assert any(isinstance(e, ToolExecutionEndEvent) for e in events)

    @pytest.mark.asyncio
    async def test_agent_error_event_on_llm_error(self):
        from helpers import error_seq
        engine = Engine(llm=FakeLLM(error_seq("boom")), tools=[])
        events = await collect_events(engine)
        assert any(isinstance(e, AgentErrorEvent) for e in events)

    @pytest.mark.asyncio
    async def test_hooks_subscriber_receives_all_events(self):
        hooks = Hooks()
        received = []
        hooks.subscribe(lambda e: received.append(e))
        engine = Engine(llm=FakeLLM(text_seq()), tools=[], hooks=hooks)
        await engine.run([UserMessage.text("go")])
        types = {type(e).__name__ for e in received}
        assert "AgentStartEvent" in types
        assert "AgentEndEvent" in types
        assert "MessageEndEvent" in types

    @pytest.mark.asyncio
    async def test_save_point_and_settled_after_agent_invoke(self):
        from helpers import make_agent
        received = []
        hooks = Hooks()
        hooks.subscribe(lambda e: received.append(e))
        agent, _ = make_agent(FakeLLM(text_seq()), hooks=hooks)
        await agent.invoke("test")
        event_types = [getattr(e, 'type', None) for e in received]
        assert 'save_point' in event_types
        assert 'settled' in event_types


class TestTurnEndDetails:
    @pytest.mark.asyncio
    async def test_turn_end_tool_results_captured_at_emit_time(self):
        """TurnEndEvent tool_results list is mutable — snapshot at emit time."""
        snapshotted = []
        async def capture(e):
            if isinstance(e, TurnEndEvent):
                snapshotted.append(list(e.tool_results))
        engine = Engine(llm=FakeLLM(tool_call_seq("t1", "t"), text_seq()), tools=[make_tool("t", "data")])
        await engine.subscribe(capture)
        await engine.run([UserMessage.text("go")])
        assert snapshotted[0], "first TurnEnd must carry tool results"
        assert snapshotted[0][0].content == "data"

    @pytest.mark.asyncio
    async def test_turn_end_message_is_assistant(self):
        turn_ends = []
        engine = Engine(llm=FakeLLM(text_seq("answer")), tools=[])
        await engine.subscribe(lambda e: turn_ends.append(e) if isinstance(e, TurnEndEvent) else None)
        await engine.run([UserMessage.text("go")])
        assert turn_ends[-1].message.role == Role.ASSISTANT
        assert "answer" in turn_ends[-1].message.text_content()

    @pytest.mark.asyncio
    async def test_turn_count_matches_tool_plus_text_turns(self):
        turn_ends = []
        engine = Engine(llm=FakeLLM(tool_call_seq("t1", "a"), tool_call_seq("t2", "b"), text_seq()),
                        tools=[make_tool("a"), make_tool("b")])
        await engine.subscribe(lambda e: turn_ends.append(e) if isinstance(e, TurnEndEvent) else None)
        await engine.run([UserMessage.text("go")])
        assert len(turn_ends) == 3

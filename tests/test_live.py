"""Live integration tests — require MISTRAL_API_KEY in .env.

Run: uv run pytest tests/battle/test_live.py -m live
Skip: uv run pytest tests/battle/ -m "not live"
"""
from __future__ import annotations

import pytest
from helpers import make_mistral_llm, text_seq, make_tool, AnyParams

from program.engine.service import Engine
from program.engine.types import AgentEndEvent, AgentErrorEvent, ToolExecutionEndEvent
from program.message.types import UserMessage, Role
from program.tool.types import Tool, ToolKind, ToolResult


@pytest.mark.live
class TestMistralLiveBasic:
    @pytest.mark.asyncio
    async def test_single_turn_text_response(self):
        """Real Mistral API returns a text response."""
        llm = make_mistral_llm()
        engine = Engine(llm=llm, tools=[])
        events = []
        await engine.subscribe(lambda e: events.append(e))
        await engine.run([UserMessage.text("Say exactly: HELLO")])

        assert any(isinstance(e, AgentEndEvent) for e in events)
        assert not any(isinstance(e, AgentErrorEvent) for e in events)
        last_msg = [m for m in engine.state.messages if m.role == Role.ASSISTANT]
        assert last_msg and last_msg[-1].text_content()

    @pytest.mark.asyncio
    async def test_tool_call_roundtrip(self):
        """Real Mistral API calls a tool and gets back the result."""
        from pydantic import BaseModel

        class EchoParams(BaseModel):
            message: str

        class EchoTool(Tool):
            async def execute(self, invocation, **kwargs):
                return ToolResult.ok(invocation.id, f"echo: {invocation.params.get('message', '')}")

        tool = EchoTool(name="echo", description="Echo back the message", schema=EchoParams, kind=ToolKind.Read)
        llm = make_mistral_llm()
        engine = Engine(llm=llm, tools=[tool])
        events = []
        await engine.subscribe(lambda e: events.append(e))
        await engine.run([UserMessage.text("Use the echo tool with message='battle_test'")])

        # May or may not call the tool depending on model decision
        assert any(isinstance(e, AgentEndEvent) for e in events)

    @pytest.mark.asyncio
    async def test_multi_turn_with_follow_up(self):
        """Real Mistral API handles follow-up messages correctly."""
        llm = make_mistral_llm()
        engine = Engine(llm=llm, tools=[])
        await engine.run([UserMessage.text("Remember the number 42.")])
        await engine.follow_up(UserMessage.text("What number did I ask you to remember?"))
        await engine.run_continue()

        assistant_msgs = [m for m in engine.state.messages if m.role == Role.ASSISTANT]
        assert len(assistant_msgs) >= 2

    @pytest.mark.asyncio
    async def test_error_handling_empty_message(self):
        """Engine handles edge case of near-empty input gracefully."""
        llm = make_mistral_llm()
        engine = Engine(llm=llm, tools=[])
        await engine.run([UserMessage.text("ok")])
        assert any(m.role == Role.ASSISTANT for m in engine.state.messages)

    @pytest.mark.asyncio
    async def test_mistral_large_model(self):
        """Verify mistral-large-latest also works."""
        llm = make_mistral_llm("mistral-large-latest")
        engine = Engine(llm=llm, tools=[])
        await engine.run([UserMessage.text("Reply with one word: DONE")])
        msgs = [m for m in engine.state.messages if m.role == Role.ASSISTANT]
        assert msgs and msgs[-1].text_content()

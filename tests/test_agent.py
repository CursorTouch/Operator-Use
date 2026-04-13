"""Tests for Agent — _clean_content, _handle_reaction, and mocked LLM loop."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from operator_use.agent.service import Agent
from operator_use.bus.views import IncomingMessage, TextPart
from operator_use.messages.service import AIMessage, HumanMessage
from operator_use.providers.events import LLMEvent, LLMEventType, ToolCall


# --- _clean_content (static, pure) ---


def test_clean_content_strips_think_block():
    result = Agent._clean_content("<think>internal reasoning</think> actual response")
    assert result == "actual response"


def test_clean_content_strips_multiline_think():
    result = Agent._clean_content("<think>\nline 1\nline 2\n</think>answer")
    assert result == "answer"


def test_clean_content_strips_msg_id_prefix():
    result = Agent._clean_content("[msg_id:123] Hello there")
    assert result == "Hello there"


def test_clean_content_strips_bot_msg_id_prefix():
    result = Agent._clean_content("[bot_msg_id:456] Reply text")
    assert result == "Reply text"


def test_clean_content_strips_ctrl_tags():
    result = Agent._clean_content("text<ctrl9>more")
    assert result == "textmore"


def test_clean_content_empty_returns_placeholder():
    result = Agent._clean_content("")
    assert result == "(no response)"


def test_clean_content_whitespace_only_returns_placeholder():
    result = Agent._clean_content("   \n  ")
    assert result == "(no response)"


def test_clean_content_no_changes_needed():
    result = Agent._clean_content("plain response")
    assert result == "plain response"


def test_clean_content_think_only_returns_placeholder():
    result = Agent._clean_content("<think>all internal</think>")
    assert result == "(no response)"


# --- Mock LLM helper ---


def make_mock_llm(text_response="Hello from agent"):
    llm = MagicMock()
    llm.model_name = "mock-llm"
    event = LLMEvent(type=LLMEventType.TEXT, content=text_response)
    llm.ainvoke = AsyncMock(return_value=event)
    llm.astream = None
    return llm


# --- Agent._handle_reaction ---


@pytest.mark.asyncio
async def test_handle_reaction_adds_emoji(tmp_path):
    llm = make_mock_llm()
    agent = Agent(llm=llm, workspace=tmp_path)

    session_id = "telegram:123"
    session = agent.sessions.get_or_create(session_id)
    ai_msg = AIMessage(content="response", metadata={"message_id": 999})
    session.add_message(ai_msg)
    agent.sessions.save(session)

    reaction_msg = IncomingMessage(
        channel="telegram",
        chat_id="123",
        parts=[TextPart(content="[reaction:👍]")],
        metadata={
            "_reaction_event": True,
            "_reaction_emojis": ["👍"],
            "_reaction_removed_emojis": [],
            "_reaction_bot_message_id": 999,
            "user_id": "user1",
        },
    )
    await agent._handle_reaction(reaction_msg)

    updated = agent.sessions.get_or_create(session_id)
    ai_msgs = [m for m in updated.messages if isinstance(m, AIMessage)]
    assert any("reactions" in m.metadata for m in ai_msgs)
    reactions = ai_msgs[-1].metadata.get("reactions", [])
    assert any("👍" in r.get("emojis", []) for r in reactions)


@pytest.mark.asyncio
async def test_handle_reaction_removes_emoji(tmp_path):
    llm = make_mock_llm()
    agent = Agent(llm=llm, workspace=tmp_path)

    session_id = "telegram:456"
    session = agent.sessions.get_or_create(session_id)
    ai_msg = AIMessage(
        content="response",
        metadata={
            "message_id": 100,
            "reactions": [{"emojis": ["👍"], "user_id": "user1", "timestamp": "2026-01-01"}],
        },
    )
    session.add_message(ai_msg)
    agent.sessions.save(session)

    reaction_msg = IncomingMessage(
        channel="telegram",
        chat_id="456",
        parts=[],
        metadata={
            "_reaction_event": True,
            "_reaction_emojis": [],
            "_reaction_removed_emojis": ["👍"],
            "_reaction_bot_message_id": 100,
            "user_id": "user1",
        },
    )
    await agent._handle_reaction(reaction_msg)

    updated = agent.sessions.get_or_create(session_id)
    ai_msgs = [m for m in updated.messages if isinstance(m, AIMessage)]
    reactions = ai_msgs[-1].metadata.get("reactions", [])
    assert not any("👍" in r.get("emojis", []) for r in reactions)


# --- Agent.run with mocked LLM ---


@pytest.mark.asyncio
async def test_agent_run_returns_ai_message(tmp_path):
    llm = make_mock_llm("Hello, world!")
    agent = Agent(llm=llm, workspace=tmp_path)

    msg = HumanMessage(content="Hi")
    response = await agent.run(message=msg, session_id="test:session")

    assert isinstance(response, AIMessage)
    assert response.content == "Hello, world!"


@pytest.mark.asyncio
async def test_agent_run_saves_session(tmp_path):
    llm = make_mock_llm("Saved reply")
    agent = Agent(llm=llm, workspace=tmp_path)

    msg = HumanMessage(content="remember this")
    await agent.run(message=msg, session_id="test:save")

    session = agent.sessions.get_or_create("test:save")
    assert any(isinstance(m, HumanMessage) for m in session.messages)
    assert any(isinstance(m, AIMessage) for m in session.messages)


@pytest.mark.asyncio
async def test_agent_run_with_tool_call_then_text(tmp_path):
    llm = MagicMock()
    llm.model_name = "mock-llm"
    llm.astream = None

    tool_event = LLMEvent(
        type=LLMEventType.TOOL_CALL,
        tool_call=ToolCall(id="t1", name="echo", params={"message": "test"}),
    )
    text_event = LLMEvent(type=LLMEventType.TEXT, content="Done!")
    llm.ainvoke = AsyncMock(side_effect=[tool_event, text_event])

    agent = Agent(llm=llm, workspace=tmp_path)

    # Register a simple echo tool
    from pydantic import BaseModel
    from operator_use.tools.service import Tool

    class EchoParams(BaseModel):
        message: str

    class EchoTool(Tool):
        def __init__(self):
            super().__init__(name="echo", description="echo", model=EchoParams)

        def __call__(self, fn):
            self.function = fn
            return self

    et = EchoTool()

    @et
    def _echo(message: str, **kwargs):
        return message

    agent.tool_register.register(et)

    msg = HumanMessage(content="run tool")
    response = await agent.run(message=msg, session_id="tool:session")
    assert response.content == "Done!"


@pytest.mark.asyncio
async def test_agent_run_max_iterations_raises(tmp_path):
    llm = MagicMock()
    llm.model_name = "mock-llm"
    llm.astream = None

    from operator_use.providers.events import LLMEvent, LLMEventType, ToolCall

    tool_event = LLMEvent(
        type=LLMEventType.TOOL_CALL, tool_call=ToolCall(id="t1", name="nonexistent_tool", params={})
    )
    llm.ainvoke = AsyncMock(return_value=tool_event)

    agent = Agent(llm=llm, workspace=tmp_path, max_iterations=2)
    msg = HumanMessage(content="loop forever")

    with pytest.raises(RuntimeError, match="max_iterations"):
        await agent.run(message=msg, session_id="loop:session")


@pytest.mark.asyncio
async def test_agent_run_hooks_fired(tmp_path):
    llm = make_mock_llm("response")
    agent = Agent(llm=llm, workspace=tmp_path)

    fired = []
    from operator_use.agent.hooks.events import HookEvent

    @agent.hooks.on(HookEvent.BEFORE_AGENT_START)
    async def on_start(ctx):
        fired.append("start")

    @agent.hooks.on(HookEvent.AFTER_AGENT_END)
    async def on_end(ctx):
        fired.append("end")

    await agent.run(message=HumanMessage(content="hi"), session_id="hook:session")
    assert "start" in fired
    assert "end" in fired

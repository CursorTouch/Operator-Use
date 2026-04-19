"""E2E smoke test — full pipeline: message in → agent processes → response out.

No real LLM API keys required. The mock_llm_provider fixture returns
scripted, deterministic responses so this suite runs safely in CI.
"""

import pytest

from tests.e2e.helpers import assert_response_contains, assert_tool_called, send_message


# ---------------------------------------------------------------------------
# Smoke: message → response pipeline (no tool calls)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smoke_send_message_returns_response(test_agent):
    """Agent must return a non-empty response for a simple text message."""
    result = await send_message(test_agent, "Hello, agent!")
    assert result.content, "Expected a non-empty response from the agent"


@pytest.mark.asyncio
async def test_smoke_response_contains_expected_text(test_agent):
    """assert_response_contains should pass when the expected substring is present."""
    result = await send_message(test_agent, "ping")
    assert_response_contains(result, "pong")


@pytest.mark.asyncio
async def test_smoke_tool_call_is_recorded(test_agent_with_echo_tool, mock_llm_with_tool_call):
    """assert_tool_called should detect that the echo tool was invoked."""
    tool_calls = await mock_llm_with_tool_call(test_agent_with_echo_tool, "run the echo tool")
    assert_tool_called(tool_calls, "echo")


@pytest.mark.asyncio
async def test_smoke_unique_sessions_are_isolated(test_agent):
    """Two different session IDs must never share history."""
    from operator_use.messages.service import HumanMessage

    await test_agent.run(message=HumanMessage(content="session A message"), session_id="e2e:session-a")
    await test_agent.run(message=HumanMessage(content="session B message"), session_id="e2e:session-b")

    session_a = test_agent.sessions.get_or_create("e2e:session-a")
    session_b = test_agent.sessions.get_or_create("e2e:session-b")

    a_texts = [m.content for m in session_a.messages]
    b_texts = [m.content for m in session_b.messages]

    assert not any("session B message" in t for t in a_texts if t)
    assert not any("session A message" in t for t in b_texts if t)


@pytest.mark.asyncio
async def test_orchestrator_routes_message_to_agent(test_orchestrator):
    """Full pipeline: orchestrator.process_direct() → agent → response.

    Uses process_direct() to invoke the full Orchestrator pipeline
    (message building → agent routing → LLM loop → response building)
    without starting the blocking bus consume loop (ainvoke).
    The mock LLM always returns "pong", so we assert the response is non-empty.
    """
    response = await test_orchestrator.process_direct("ping", channel="cli", chat_id="e2e:orch")
    assert response, "Orchestrator must return a non-empty response for a simple text message"

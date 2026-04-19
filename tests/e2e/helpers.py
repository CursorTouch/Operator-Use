"""E2E test helper utilities.

Provides three high-level assertions and a message-injection helper so that
e2e tests can read as plain English rather than as plumbing code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from operator_use.agent.service import Agent
    from operator_use.messages.service import AIMessage


async def send_message(agent: "Agent", text: str, session_id: str = "e2e:default") -> "AIMessage":
    """Inject a plain-text message into *agent* and return the AIMessage response.

    No channels, no bus, no real LLM — the agent fixture uses a mock LLM that
    returns deterministic responses, so this helper is CI-safe.

    Args:
        agent:      A fully-configured Agent (mock LLM wired in).
        text:       The human turn text to send.
        session_id: Session identifier; defaults to an isolated e2e key.

    Returns:
        The AIMessage produced by the agent's agentic loop.
    """
    from operator_use.messages.service import HumanMessage

    msg = HumanMessage(content=text)
    return await agent.run(message=msg, session_id=session_id)


def assert_response_contains(response: "AIMessage", expected: str) -> None:
    """Assert that *response.content* contains *expected* as a substring.

    Args:
        response: AIMessage returned by send_message() or agent.run().
        expected: Substring that must appear in the response content.

    Raises:
        AssertionError: When the substring is absent.
    """
    content = response.content or ""
    assert expected in content, (
        f"Expected response to contain {expected!r}, got: {content!r}"
    )


def assert_tool_called(tool_calls: list[str], tool_name: str) -> None:
    """Assert that *tool_name* appears in the list of recorded tool call names.

    The list is produced by the ``mock_llm_with_tool_call`` fixture which
    collects tool names as the agent loop executes them.

    Args:
        tool_calls: List of tool names that were invoked during the run.
        tool_name:  The tool name that must appear at least once.

    Raises:
        AssertionError: When the tool was not called.
    """
    assert tool_name in tool_calls, (
        f"Expected tool {tool_name!r} to be called; called tools: {tool_calls!r}"
    )

from unittest.mock import AsyncMock, MagicMock

import pytest

from operator_use.tools.local_agents import LOCAL_AGENT_DELEGATION_CHAIN, localagents
from operator_use.messages.service import AIMessage


def make_target_agent(
    response_text: str = "delegated result", description: str = "Research specialist"
):
    agent = MagicMock()
    agent.description = description
    agent.get_plugin.side_effect = lambda name: None
    agent.run = AsyncMock(return_value=AIMessage(content=response_text))
    return agent


@pytest.mark.asyncio
async def test_localagents_lists_available_agents():
    current = make_target_agent(description="Manager")
    target = make_target_agent(description="Browser specialist")

    result = await localagents.ainvoke(
        action="agents",
        _agent=current,
        _agent_id="manager",
        _agent_registry={"manager": current, "browser": target},
    )

    assert result.success is True
    assert "manager (current)" in result.output
    assert "Browser specialist" in result.output


@pytest.mark.asyncio
async def test_localagents_runs_target_agent():
    current = make_target_agent(description="Manager")
    target = make_target_agent(response_text="done", description="Writer")

    result = await localagents.ainvoke(
        action="run",
        name="writer",
        task="Draft a summary",
        _agent=current,
        _agent_id="manager",
        _session_id="chat:42",
        _agent_registry={"manager": current, "writer": target},
    )

    assert result.success is True
    assert result.output == "done"
    target.run.assert_awaited_once()
    delegated_metadata = target.run.await_args.kwargs["incoming"].metadata
    assert delegated_metadata[LOCAL_AGENT_DELEGATION_CHAIN] == ["manager", "writer"]


@pytest.mark.asyncio
async def test_localagents_refuses_self_delegation():
    current = make_target_agent(description="Manager")

    result = await localagents.ainvoke(
        action="run",
        name="manager",
        task="Do it yourself",
        _agent=current,
        _agent_id="manager",
        _agent_registry={"manager": current},
    )

    assert result.success is False
    assert "Refusing to delegate" in result.error


@pytest.mark.asyncio
async def test_localagents_refuses_indirect_circular_delegation():
    manager = make_target_agent(description="Manager")
    browser = make_target_agent(description="Browser specialist")

    result = await localagents.ainvoke(
        action="run",
        name="manager",
        task="Bounce this back",
        _agent=browser,
        _agent_id="browser",
        _metadata={LOCAL_AGENT_DELEGATION_CHAIN: ["manager", "browser"]},
        _agent_registry={"manager": manager, "browser": browser},
    )

    assert result.success is False
    assert "Refusing circular local delegation" in result.error
    assert "manager -> browser -> manager" in result.error
    manager.run.assert_not_awaited()


# ---------------------------------------------------------------------------
# Channel / chat_id propagation (fix: subagent result routing)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_localagents_propagates_parent_channel_and_chat_id():
    """IncomingMessage passed to target must carry the parent's real channel/chat_id
    so any subagents spawned by the local agent route results back correctly."""
    current = make_target_agent(description="Manager")
    target = make_target_agent(response_text="done", description="Worker")

    result = await localagents.ainvoke(
        action="run",
        name="worker",
        task="Do something",
        _agent=current,
        _agent_id="manager",
        _session_id="telegram:99",
        _channel="telegram",
        _chat_id="99",
        _agent_registry={"manager": current, "worker": target},
    )

    assert result.success is True
    incoming = target.run.await_args.kwargs["incoming"]
    assert incoming.channel == "telegram"
    assert incoming.chat_id == "99"


@pytest.mark.asyncio
async def test_localagents_falls_back_to_direct_when_no_parent_channel():
    """When called without _channel/_chat_id (e.g. from a test), channel defaults
    to 'direct' and chat_id falls back to the parent session id."""
    current = make_target_agent(description="Manager")
    target = make_target_agent(response_text="done", description="Worker")

    result = await localagents.ainvoke(
        action="run",
        name="worker",
        task="Do something",
        _agent=current,
        _agent_id="manager",
        _session_id="fallback-session",
        _agent_registry={"manager": current, "worker": target},
    )

    assert result.success is True
    incoming = target.run.await_args.kwargs["incoming"]
    assert incoming.channel == "direct"
    assert incoming.chat_id == "fallback-session"


@pytest.mark.asyncio
async def test_localagents_session_id_stays_isolated_despite_channel_propagation():
    """The delegated session_id must remain isolated even though channel/chat_id
    are the parent's real values."""
    current = make_target_agent(description="Manager")
    target = make_target_agent(response_text="done", description="Worker")

    await localagents.ainvoke(
        action="run",
        name="worker",
        task="Do something",
        _agent=current,
        _agent_id="manager",
        _session_id="telegram:99",
        _channel="telegram",
        _chat_id="99",
        _agent_registry={"manager": current, "worker": target},
    )

    call_kwargs = target.run.await_args.kwargs
    assert call_kwargs["session_id"] != "telegram:99"  # isolated session
    assert "manager-to-worker" in call_kwargs["session_id"]  # namespaced
    assert call_kwargs["incoming"].channel == "telegram"  # but routing uses real channel

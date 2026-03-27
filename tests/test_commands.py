"""Tests for orchestrator/commands.py — session and system control commands."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from operator_use.bus.service import Bus
from operator_use.bus.views import IncomingMessage, OutgoingMessage, TextPart
from operator_use.messages.service import HumanMessage, AIMessage
from operator_use.orchestrator.commands import handle_command, COMMANDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_agent(tmp_path, messages=None):
    """Return a mock agent backed by a real SessionStore."""
    from operator_use.session.service import SessionStore
    store = SessionStore(tmp_path)
    agent = MagicMock()
    agent.sessions = store
    return agent


def make_message(command: str, channel="telegram", chat_id="123"):
    return IncomingMessage(
        channel=channel,
        chat_id=chat_id,
        parts=[TextPart(content=f"/{command}")],
        user_id="user1",
        metadata={"_command": command},
    )


# ---------------------------------------------------------------------------
# COMMANDS constant
# ---------------------------------------------------------------------------

def test_commands_contains_required():
    assert {"start", "stop", "restart"}.issubset(COMMANDS)


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_no_existing_session(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)
    msg = make_message("start")

    await handle_command(msg, agent, bus)

    outgoing: OutgoingMessage = await bus.consume_outgoing()
    text = outgoing.parts[0].content
    assert "started" in text.lower()


@pytest.mark.asyncio
async def test_start_with_active_session(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)

    # Pre-populate an active session
    session = agent.sessions.get_or_create("telegram:123")
    session.add_message(HumanMessage(content="existing message"))
    agent.sessions.save(session)

    msg = make_message("start")
    await handle_command(msg, agent, bus)

    outgoing: OutgoingMessage = await bus.consume_outgoing()
    text = outgoing.parts[0].content
    assert "already active" in text.lower()


@pytest.mark.asyncio
async def test_start_after_stop_starts_fresh(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)

    session = agent.sessions.get_or_create("telegram:123")
    session.add_message(HumanMessage(content="old message"))
    agent.sessions.save(session)

    # Stop archives the session
    await handle_command(make_message("stop"), agent, bus)
    await bus.consume_outgoing()  # discard stop response

    # Now start should see no active session
    await handle_command(make_message("start"), agent, bus)
    outgoing: OutgoingMessage = await bus.consume_outgoing()
    assert "started" in outgoing.parts[0].content.lower()


# ---------------------------------------------------------------------------
# /stop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_archives_session(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)

    session = agent.sessions.get_or_create("telegram:123")
    session.add_message(HumanMessage(content="message to archive"))
    agent.sessions.save(session)

    await handle_command(make_message("stop"), agent, bus)

    outgoing: OutgoingMessage = await bus.consume_outgoing()
    assert "stopped" in outgoing.parts[0].content.lower()

    # Active slot is gone
    assert agent.sessions.load("telegram:123") is None

    # Archived file exists
    archived = list((tmp_path / "sessions").glob("telegram_123_archived_*.jsonl"))
    assert len(archived) == 1


@pytest.mark.asyncio
async def test_stop_empty_session(tmp_path):
    """Stopping with no session should still respond without error."""
    bus = Bus()
    agent = make_agent(tmp_path)

    await handle_command(make_message("stop"), agent, bus)

    outgoing: OutgoingMessage = await bus.consume_outgoing()
    assert outgoing is not None


# ---------------------------------------------------------------------------
# /restart
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_restart_sends_restarting_message(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)

    with patch("operator_use.orchestrator.commands._deferred_process_restart", new=AsyncMock()):
        await handle_command(make_message("restart"), agent, bus)

    outgoing: OutgoingMessage = await bus.consume_outgoing()
    assert "restart" in outgoing.parts[0].content.lower()


@pytest.mark.asyncio
async def test_restart_schedules_process_restart(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)

    mock_deferred = AsyncMock()
    with patch("operator_use.orchestrator.commands._deferred_process_restart", new=mock_deferred):
        with patch("operator_use.orchestrator.commands.asyncio.create_task") as mock_create_task:
            mock_create_task.side_effect = lambda coro: coro.close()
            await handle_command(make_message("restart"), agent, bus)
            mock_create_task.assert_called_once()


# ---------------------------------------------------------------------------
# Unknown command
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_command_does_nothing(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)
    msg = IncomingMessage(
        channel="telegram", chat_id="123",
        parts=[TextPart(content="/unknown")],
        metadata={"_command": "unknown"},
    )

    await handle_command(msg, agent, bus)

    # Nothing published to bus
    with pytest.raises(Exception):
        await asyncio.wait_for(bus.consume_outgoing(), timeout=0.1)


# ---------------------------------------------------------------------------
# Response routing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_response_routed_to_correct_channel(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)
    msg = make_message("stop", channel="discord", chat_id="456")

    await handle_command(msg, agent, bus)

    outgoing: OutgoingMessage = await bus.consume_outgoing()
    assert outgoing.channel == "discord"
    assert outgoing.chat_id == "456"
    assert outgoing.reply is True


import asyncio

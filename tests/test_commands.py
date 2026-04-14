"""Tests for orchestrator/commands.py — session and system control commands."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from operator_use.bus.service import Bus
from operator_use.bus.views import IncomingMessage, OutgoingMessage, TextPart
from operator_use.messages.service import HumanMessage
from operator_use.orchestrator.commands import handle_command, COMMANDS, _HELP_TEXT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_agent(tmp_path):
    from operator_use.session.service import SessionStore

    store = SessionStore(tmp_path)
    agent = MagicMock()
    agent.sessions = store
    agent.gateway = None
    agent.tool_register._extensions = {}
    return agent


def make_message(command: str, args: str = "", channel="telegram", chat_id="123"):
    return IncomingMessage(
        channel=channel,
        chat_id=chat_id,
        parts=[TextPart(content=f"/{command}" + (f" {args}" if args else ""))],
        user_id="user1",
        metadata={"_command": command, "_command_args": args},
    )


def make_overrides() -> dict:
    return {}


# ---------------------------------------------------------------------------
# COMMANDS constant
# ---------------------------------------------------------------------------


def test_commands_contains_required():
    assert {"start", "stop", "restart"}.issubset(COMMANDS)


# ---------------------------------------------------------------------------
# /start — default session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_new_session_greeting(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)

    await handle_command(make_message("start"), agent, bus, make_overrides())

    text = (await bus.consume_outgoing()).parts[0].content
    assert "started" in text.lower()
    assert _HELP_TEXT in text


@pytest.mark.asyncio
async def test_start_already_active(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)
    session = agent.sessions.get_or_create("telegram:123")
    session.add_message(HumanMessage(content="hi"))
    agent.sessions.save(session)

    await handle_command(make_message("start"), agent, bus, make_overrides())

    text = (await bus.consume_outgoing()).parts[0].content
    assert "already active" in text.lower()


@pytest.mark.asyncio
async def test_start_after_stop_starts_fresh(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)
    overrides = make_overrides()

    session = agent.sessions.get_or_create("telegram:123")
    session.add_message(HumanMessage(content="old"))
    agent.sessions.save(session)

    await handle_command(make_message("stop"), agent, bus, overrides)
    await bus.consume_outgoing()

    await handle_command(make_message("start"), agent, bus, overrides)
    text = (await bus.consume_outgoing()).parts[0].content
    assert "started" in text.lower()


# ---------------------------------------------------------------------------
# /start <name> — named sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_named_session_greeting(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)
    overrides = make_overrides()

    await handle_command(make_message("start", args="work"), agent, bus, overrides)

    text = (await bus.consume_outgoing()).parts[0].content
    assert "'work'" in text
    assert "started" in text.lower()


@pytest.mark.asyncio
async def test_start_named_session_sets_override(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)
    overrides = make_overrides()

    await handle_command(make_message("start", args="work"), agent, bus, overrides)
    await bus.consume_outgoing()

    assert overrides.get("telegram:123") == "telegram:123:work"


@pytest.mark.asyncio
async def test_start_default_clears_override(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)
    overrides = {"telegram:123": "telegram:123:work"}

    await handle_command(make_message("start"), agent, bus, overrides)
    await bus.consume_outgoing()

    assert "telegram:123" not in overrides


@pytest.mark.asyncio
async def test_named_session_stored_separately(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)
    overrides = make_overrides()

    # Start named session, add a message via stop (archives it)
    # First verify named session ID is used
    await handle_command(make_message("start", args="project"), agent, bus, overrides)
    await bus.consume_outgoing()

    assert overrides["telegram:123"] == "telegram:123:project"
    # The named session is separate from the default one
    assert agent.sessions.load("telegram:123") is None
    assert agent.sessions.load("telegram:123:project") is None  # empty, not yet saved


# ---------------------------------------------------------------------------
# /stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_archives_session(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)
    session = agent.sessions.get_or_create("telegram:123")
    session.add_message(HumanMessage(content="message"))
    agent.sessions.save(session)

    await handle_command(make_message("stop"), agent, bus, make_overrides())

    text = (await bus.consume_outgoing()).parts[0].content
    assert "saved" in text.lower()
    assert agent.sessions.load("telegram:123") is None
    archived = list((tmp_path / "sessions").glob("telegram_123_archived_*.jsonl"))
    assert len(archived) == 1


@pytest.mark.asyncio
async def test_stop_archives_named_session(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)
    overrides = {"telegram:123": "telegram:123:work"}

    session = agent.sessions.get_or_create("telegram:123:work")
    session.add_message(HumanMessage(content="work message"))
    agent.sessions.save(session)

    await handle_command(make_message("stop"), agent, bus, overrides)
    await bus.consume_outgoing()

    # Named session archived, override cleared
    assert agent.sessions.load("telegram:123:work") is None
    assert "telegram:123" not in overrides
    archived = list((tmp_path / "sessions").glob("telegram_123_work_archived_*.jsonl"))
    assert len(archived) == 1


@pytest.mark.asyncio
async def test_stop_empty_session(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)

    await handle_command(make_message("stop"), agent, bus, make_overrides())

    assert (await bus.consume_outgoing()) is not None


# ---------------------------------------------------------------------------
# /restart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_sends_restarting_message(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)
    agent.gateway = MagicMock(on_restart=AsyncMock())

    with patch("operator_use.orchestrator.commands.asyncio.ensure_future") as mock_ensure:
        mock_ensure.side_effect = lambda coro: coro.close()
        with patch("operator_use.orchestrator.commands._save_restart_notification"):
            await handle_command(make_message("restart"), agent, bus, make_overrides())

    text = (await bus.consume_outgoing()).parts[0].content
    assert "restart" in text.lower()


@pytest.mark.asyncio
async def test_restart_calls_gateway_on_restart(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)
    on_restart = AsyncMock()
    agent.gateway = MagicMock(on_restart=on_restart)

    with patch("operator_use.orchestrator.commands.asyncio.ensure_future") as mock_ensure:
        mock_ensure.side_effect = lambda coro: coro.close()
        with patch("operator_use.orchestrator.commands._save_restart_notification"):
            await handle_command(make_message("restart"), agent, bus, make_overrides())

    on_restart.assert_called_once()


@pytest.mark.asyncio
async def test_restart_saves_notification(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)
    agent.gateway = MagicMock(on_restart=AsyncMock())

    with patch("operator_use.orchestrator.commands.asyncio.ensure_future") as mock_ensure:
        mock_ensure.side_effect = lambda coro: coro.close()
        with patch("operator_use.orchestrator.commands._save_restart_notification") as mock_save:
            await handle_command(
                make_message("restart", channel="discord", chat_id="999"),
                agent,
                bus,
                make_overrides(),
            )
            mock_save.assert_called_once_with("discord", "999", "")


@pytest.mark.asyncio
async def test_restart_no_gateway_logs_warning(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)
    agent.gateway = None

    with patch("operator_use.orchestrator.commands._save_restart_notification"):
        with patch("operator_use.orchestrator.commands.logger") as mock_logger:
            await handle_command(make_message("restart"), agent, bus, make_overrides())
            mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# Unknown command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_command_does_nothing(tmp_path):
    import asyncio

    bus = Bus()
    agent = make_agent(tmp_path)
    msg = IncomingMessage(
        channel="telegram",
        chat_id="123",
        parts=[TextPart(content="/unknown")],
        metadata={"_command": "unknown"},
    )

    await handle_command(msg, agent, bus, make_overrides())

    with pytest.raises(Exception):
        await asyncio.wait_for(bus.consume_outgoing(), timeout=0.1)


# ---------------------------------------------------------------------------
# Response routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_response_routed_to_correct_channel(tmp_path):
    bus = Bus()
    agent = make_agent(tmp_path)

    await handle_command(
        make_message("stop", channel="discord", chat_id="456"), agent, bus, make_overrides()
    )

    outgoing: OutgoingMessage = await bus.consume_outgoing()
    assert outgoing.channel == "discord"
    assert outgoing.chat_id == "456"
    assert outgoing.reply is True

"""Session and system control commands (/start, /stop, /restart, ...)."""

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from operator_use.bus.views import IncomingMessage, OutgoingMessage, TextPart

if TYPE_CHECKING:
    from operator_use.bus import Bus
    from operator_use.agent.service import Agent

logger = logging.getLogger(__name__)

# All recognised command names. Channels use this to detect commands.
COMMANDS = {"start", "stop", "restart"}

_HELP_TEXT = (
    "Commands:\n"
    "  /start [name] — Start a new session\n"
    "  /stop         — Save and end the current session\n"
    "  /restart      — Restart the system"
)


async def handle_command(
    message: IncomingMessage,
    agent: "Agent",
    bus: "Bus",
    session_overrides: "dict[str, str]",
) -> None:
    """Dispatch a session/system control command and publish the response."""
    command = (message.metadata or {}).get("_command")
    args = ((message.metadata or {}).get("_command_args") or "").strip()
    default_session_id = f"{message.channel}:{message.chat_id}"
    active_session_id = session_overrides.get(default_session_id, default_session_id)

    if command == "start":
        session_name = args or None
        target_session_id = (
            f"{default_session_id}:{session_name}" if session_name else default_session_id
        )
        text = await _cmd_start(target_session_id, agent, session_name)
        if session_name:
            session_overrides[default_session_id] = target_session_id
        else:
            session_overrides.pop(default_session_id, None)
    elif command == "stop":
        session_overrides.pop(default_session_id, None)
        text = await _cmd_stop(active_session_id, agent)
    elif command == "restart":
        text = await _cmd_restart(agent, message)
    else:
        logger.warning("handle_command called with unknown command: %r", command)
        return

    await bus.publish_outgoing(
        OutgoingMessage(
            chat_id=message.chat_id,
            channel=message.channel,
            account_id=message.account_id,
            parts=[TextPart(content=text)],
            metadata=message.metadata,
            reply=True,
        )
    )


# ---------------------------------------------------------------------------
# Individual command implementations
# ---------------------------------------------------------------------------


async def _cmd_start(session_id: str, agent: "Agent", session_name: str | None = None) -> str:
    existing = agent.sessions.load(session_id)
    name_label = f" '{session_name}'" if session_name else ""
    if existing and existing.messages:
        return f"Session{name_label} is already active. Use /stop to end it first."
    return f"Session{name_label} started.\n\n{_HELP_TEXT}"


async def _cmd_stop(session_id: str, agent: "Agent") -> str:
    agent.sessions.archive(session_id)
    return "Session saved and closed.\nUse /start to begin a new session."


async def _cmd_restart(agent: "Agent", message: IncomingMessage) -> str:
    _save_restart_notification(message.channel, message.chat_id, message.account_id or "")
    on_restart = getattr(getattr(agent, "gateway", None), "on_restart", None)
    if callable(on_restart):
        asyncio.ensure_future(on_restart())
    else:
        logger.warning("gateway.on_restart not configured — restart skipped")
    return "Restarting system. I'll be back in a moment."


def _save_restart_notification(channel: str, chat_id: str, account_id: str) -> None:
    """Persist the channel to notify after the process restarts."""
    try:
        from operator_use.agent.tools.builtin.control_center import RESTART_FILE

        data = json.loads(RESTART_FILE.read_text()) if RESTART_FILE.exists() else {}
        data["notify_restart"] = {"channel": channel, "chat_id": chat_id, "account_id": account_id}
        RESTART_FILE.parent.mkdir(parents=True, exist_ok=True)
        RESTART_FILE.write_text(json.dumps(data))
    except Exception:
        logger.warning("Could not save restart notification target", exc_info=True)

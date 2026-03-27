"""Session and system control commands (/start, /stop, /restart, ...)."""

import asyncio
import logging
import os
import sys
from typing import TYPE_CHECKING

from operator_use.bus.views import IncomingMessage, OutgoingMessage, TextPart

if TYPE_CHECKING:
    from operator_use.bus import Bus
    from operator_use.agent.service import Agent

logger = logging.getLogger(__name__)

# All recognised command names. Channels use this to detect commands.
COMMANDS = {"start", "stop", "restart"}


async def handle_command(
    message: IncomingMessage,
    agent: "Agent",
    bus: "Bus",
) -> None:
    """Dispatch a session/system control command and publish the response."""
    command = (message.metadata or {}).get("_command")
    session_id = f"{message.channel}:{message.chat_id}"

    if command == "start":
        text = await _cmd_start(session_id, agent)
    elif command == "stop":
        text = await _cmd_stop(session_id, agent)
    elif command == "restart":
        text = await _cmd_restart()
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

    if command == "restart":
        asyncio.create_task(_deferred_process_restart())


# ---------------------------------------------------------------------------
# Individual command implementations
# ---------------------------------------------------------------------------

async def _cmd_start(session_id: str, agent: "Agent") -> str:
    existing = agent.sessions.load(session_id)
    if existing and existing.messages:
        return "Session is already active. Use /stop to end the session."
    return "Session started! Send me a message to get started."


async def _cmd_stop(session_id: str, agent: "Agent") -> str:
    agent.sessions.archive(session_id)
    return "Session stopped and saved. Use /start to begin a new session."


async def _cmd_restart() -> str:
    return "Restarting system..."


async def _deferred_process_restart(delay: float = 1.5) -> None:
    """Wait for the response to be delivered, then replace the process."""
    await asyncio.sleep(delay)
    logger.info("Restarting process: %s %s", sys.executable, sys.argv)
    os.execv(sys.executable, [sys.executable] + sys.argv)

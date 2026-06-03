from __future__ import annotations

from typing import TYPE_CHECKING

from operator_use.commands.types import SlashCommandInfo

if TYPE_CHECKING:
    from operator_use.commands.registry import CommandRegistry


async def _handle_stop(registry: CommandRegistry, args: list[str]) -> None:
    """Execute the command with parsed arguments."""
    runtime = registry.runtime
    if runtime is None:
        print("No runtime attached.")
        return

    agent = runtime.current_session
    if agent is None:
        print("No active session.")
        return

    if agent.is_idle():
        print("Agent is already idle — nothing to stop.")
        return

    agent.abort()
    print("Stop signal sent. The agent will halt at the next safe checkpoint.")


command = SlashCommandInfo(
    name='stop',
    description='Immediately cancel the current agent operation.',
    aliases=['cancel'],
    handler=_handle_stop,
)

from __future__ import annotations

from typing import TYPE_CHECKING

from operator_use.commands.types import SlashCommandInfo

if TYPE_CHECKING:
    from operator_use.commands.registry import CommandRegistry


async def _handle_new(registry: CommandRegistry, args: list[str]) -> None:
    """Execute the command with parsed arguments."""
    runtime = registry.runtime
    if runtime is None:
        return
    await runtime.new_session()


command = SlashCommandInfo(
    name='new',
    description='Start a new session (clears history).',
    handler=_handle_new,
    aliases=['clear'],
)

from __future__ import annotations

from typing import TYPE_CHECKING

from program.commands.types import SlashCommandInfo

if TYPE_CHECKING:
    from program.commands.registry import CommandRegistry


async def _handle_reload(registry: CommandRegistry, args: list[str]) -> None:
    runtime = registry.runtime
    if runtime is None:
        return
    print("Reloading agent...")
    await runtime.reload()
    print("Agent reloaded. Session history preserved.")


command = SlashCommandInfo(
    name='reload',
    description='Reload all resources (tools, skills, commands, extensions) while keeping the active session history intact.',
    handler=_handle_reload,
)

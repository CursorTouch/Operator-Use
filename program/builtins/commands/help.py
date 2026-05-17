from __future__ import annotations

from typing import TYPE_CHECKING

from program.commands.types import SlashCommandInfo

if TYPE_CHECKING:
    from program.commands.registry import CommandRegistry


async def _handle_help(registry: CommandRegistry, args: list[str]) -> None:
    lines = ['Available commands:']
    for cmd in registry.list():
        aliases = f"  (aliases: /{', /'.join(cmd.aliases)})" if cmd.aliases else ""
        lines.append(f"  /{cmd.name} — {cmd.description}{aliases}")
    print('\n'.join(lines))


command = SlashCommandInfo(
    name='help',
    description='Show available slash commands.',
    handler=_handle_help,
    aliases=['?'],
)

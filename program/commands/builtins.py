from __future__ import annotations

from typing import TYPE_CHECKING

from program.commands.types import SlashCommandInfo

if TYPE_CHECKING:
    from program.commands.registry import CommandRegistry


async def _handle_compact(registry: CommandRegistry, args: list[str]) -> None:
    """Trigger compaction on the current session immediately."""
    custom_instructions = ' '.join(args) if args else None
    runtime = registry.runtime
    if runtime is None or runtime.current_session is None:
        return
    from program.extension.types import CompactOptions
    runtime.current_session.compact(CompactOptions(custom_instructions=custom_instructions))


async def _handle_new(registry: CommandRegistry, args: list[str]) -> None:
    """Start a new session."""
    runtime = registry.runtime
    if runtime is None:
        return
    await runtime.new_session()


async def _handle_help(registry: CommandRegistry, args: list[str]) -> None:
    """List all available commands."""
    lines = ['Available commands:']
    for cmd in registry.list():
        aliases = f"  (aliases: /{', /'.join(cmd.aliases)})" if cmd.aliases else ""
        lines.append(f"  /{cmd.name} — {cmd.description}{aliases}")
    print('\n'.join(lines))


BUILTIN_COMMANDS: list[SlashCommandInfo] = [
    SlashCommandInfo(
        name='compact',
        description='Summarise and compact the conversation history. Optionally pass custom instructions.',
        handler=_handle_compact,
    ),
    SlashCommandInfo(
        name='new',
        description='Start a new session (clears history).',
        handler=_handle_new,
        aliases=['clear'],
    ),
    SlashCommandInfo(
        name='help',
        description='Show available slash commands.',
        handler=_handle_help,
        aliases=['?'],
    ),
]

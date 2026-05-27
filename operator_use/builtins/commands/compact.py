from __future__ import annotations

from typing import TYPE_CHECKING

from operator_use.commands.types import SlashCommandInfo

if TYPE_CHECKING:
    from operator_use.commands.registry import CommandRegistry


async def _handle_compact(registry: CommandRegistry, args: list[str]) -> None:
    custom_instructions = ' '.join(args) if args else None
    runtime = registry.runtime
    if runtime is None or runtime.current_session is None:
        return
    performed = await runtime.current_session.run_compaction(custom_instructions)
    if not performed:
        print("Nothing to compact.")


command = SlashCommandInfo(
    name='compact',
    description='Summarise and compact the conversation history. Optionally pass custom instructions.',
    handler=_handle_compact,
)

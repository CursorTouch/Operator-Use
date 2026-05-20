from __future__ import annotations

from typing import TYPE_CHECKING

from program.commands.types import SlashCommandInfo

if TYPE_CHECKING:
    from program.commands.registry import CommandRegistry


async def _handle_start(registry: CommandRegistry, args: list[str]) -> None:
    print(
        "Hi! I'm Operator, a stateful AI agent.\n"
        "I can help you with tasks, answer questions, and run tools.\n"
        "Type /help to see all available commands."
    )


command = SlashCommandInfo(
    name='start',
    description='Introduce the agent.',
    handler=_handle_start,
)

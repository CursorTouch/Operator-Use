from __future__ import annotations

from typing import TYPE_CHECKING

from operator_use.agent.types import PromptOptions
from operator_use.commands.types import SlashCommandInfo

if TYPE_CHECKING:
    from operator_use.commands.registry import CommandRegistry


async def _handle_queue(registry: CommandRegistry, args: list[str]) -> None:
    runtime = registry.runtime
    if runtime is None:
        print("No active runtime.")
        return

    if not args:
        print("Usage: /queue <prompt>  — queue a prompt to run after the current turn completes.")
        return

    prompt = " ".join(args).strip()
    await runtime.invoke(prompt, PromptOptions(source='queue'))
    session = runtime.current_session
    if session and not session.is_idle():
        print(f"Queued (will run after current turn): {prompt[:80]}")
    else:
        print(f"Queued: {prompt[:80]}")


command = SlashCommandInfo(
    name='queue',
    description='Queue a prompt to run after the current agent turn completes.',
    handler=_handle_queue,
)

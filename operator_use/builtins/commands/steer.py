from __future__ import annotations

from typing import TYPE_CHECKING

from operator_use.commands.types import SlashCommandInfo

if TYPE_CHECKING:
    from operator_use.commands.registry import CommandRegistry


async def _handle_steer(registry: CommandRegistry, args: list[str]) -> None:
    """Execute the command with parsed arguments."""
    runtime = registry.runtime
    if runtime is None:
        print("No runtime attached.")
        return

    agent = runtime.current_session
    if agent is None:
        print("No active session.")
        return

    if not args:
        print("Usage: /steer <guidance to inject after next tool call>")
        return

    prompt = " ".join(args)

    if agent.is_idle():
        print("Agent is idle — steer queued and will apply on the next turn.")

    await agent.steer(prompt)
    print(f"Steer queued: {prompt!r}")


command = SlashCommandInfo(
    name='steer',
    description='Inject guidance after the next tool call without interrupting the current turn.',
    aliases=[],
    handler=_handle_steer,
)

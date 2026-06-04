from __future__ import annotations

from typing import TYPE_CHECKING

from operator_use.commands.types import SlashCommandInfo

if TYPE_CHECKING:
    from operator_use.commands.registry import CommandRegistry

_USAGE = "Usage: /background <prompt>  — run a task in a background session (aliases: /bg, /btw)"


async def _handle_background(registry: CommandRegistry, args: list[str]) -> None:
    runtime = registry.runtime
    if runtime is None:
        print("No active runtime.")
        return

    if not args:
        print(_USAGE)
        return

    prompt = " ".join(args).strip()
    subagent_manager = getattr(runtime, 'subagent_manager', None)
    if subagent_manager is None:
        print("Subagent manager is not available.")
        return

    task_id = await subagent_manager.invoke(
        task=prompt,
        label=prompt[:60],
        fork=True,
        deliver='agent',
    )
    print(f"Background task started (id={task_id[:8]}).")
    print(f"Task: {prompt[:80]}")
    print("Result will surface in this session when complete.")


command = SlashCommandInfo(
    name='background',
    description='Run a prompt in a background session without blocking the current workspace.',
    aliases=['bg', 'btw'],
    handler=_handle_background,
)

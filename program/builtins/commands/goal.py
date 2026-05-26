from __future__ import annotations

from typing import TYPE_CHECKING

from program.agent.types import PromptOptions
from program.commands.types import SlashCommandInfo

if TYPE_CHECKING:
    from program.commands.registry import CommandRegistry


async def _handle_goal(registry: CommandRegistry, args: list[str]) -> None:
    runtime = registry.runtime
    if runtime is None or runtime.current_session is None:
        return

    manager = runtime.current_session.goal_manager
    action = args[0].lower() if args else "status"

    if action in {"status", "show"}:
        print(manager.status_line())
        return

    if action == "pause":
        manager.pause()
        print(manager.status_line())
        return

    if action == "resume":
        state = manager.resume()
        if state is None:
            print("No active goal. Set one with /goal <text>.")
            return
        print(manager.status_line())
        prompt = manager.next_continuation_prompt()
        if prompt:
            await runtime.invoke(prompt, PromptOptions(source='goal'))
        return

    if action in {"clear", "stop", "done"}:
        manager.clear()
        print("Goal cleared.")
        return

    goal = " ".join(args).strip()
    if not goal:
        print(manager.status_line())
        return

    state = manager.set(goal)
    print(f"Goal set ({state.max_turns}-turn budget): {state.goal}")
    await runtime.invoke(state.goal, PromptOptions(source='goal'))


command = SlashCommandInfo(
    name='goal',
    description='Set a standing goal and keep working across turns until it is achieved.',
    handler=_handle_goal,
)

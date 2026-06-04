from __future__ import annotations

from typing import TYPE_CHECKING

from operator_use.commands.types import SlashCommandInfo

if TYPE_CHECKING:
    from operator_use.commands.registry import CommandRegistry


async def _handle_subgoal(registry: CommandRegistry, args: list[str]) -> None:
    runtime = registry.runtime
    if runtime is None or runtime.current_session is None:
        return

    if not args:
        print("Usage: /subgoal <criterion>  — add a required criterion to the active goal.")
        return

    manager = runtime.current_session.goal_manager
    criterion = " ".join(args).strip()
    state = manager.add_subgoal(criterion)

    if state is None:
        print("No active goal. Set one with /goal <text> first.")
        return

    print(f"Criterion added ({len(state.subgoals)} total): {criterion}")
    print(manager.status_line())


command = SlashCommandInfo(
    name='subgoal',
    description='Add a required criterion to the active goal. The judge must satisfy all criteria before marking done.',
    handler=_handle_subgoal,
)

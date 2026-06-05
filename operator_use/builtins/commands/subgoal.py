from __future__ import annotations

from typing import TYPE_CHECKING

from operator_use.commands.types import SlashCommandInfo

if TYPE_CHECKING:
    from operator_use.commands.registry import CommandRegistry


async def _handle_subgoal(registry: CommandRegistry, args: list[str]) -> None:
    runtime = registry.runtime
    if runtime is None or runtime.current_session is None:
        return

    manager = runtime.current_session.goal_manager

    action = args[0].lower() if args else "status"

    match action:
        case "status" | "list" | "show":
            if not manager.has_goal():
                print("No active goal. Set one with /goal <text> first.")
                return
            subgoals = manager.state.subgoals if manager.state else []
            if not subgoals:
                print("No subgoals. Add one with /subgoal <criterion>.")
            else:
                for i, s in enumerate(subgoals, 1):
                    print(f"  {i}. {s}")

        case "clear":
            try:
                count = manager.clear_subgoals()
            except RuntimeError:
                print("No active goal. Set one with /goal <text> first.")
                return
            print(f"Cleared {count} subgoal{'s' if count != 1 else ''}.")

        case "remove":
            if len(args) < 2:
                print("Usage: /subgoal remove <N>")
                return
            try:
                removed = manager.remove_subgoal(int(args[1]))
            except (ValueError, TypeError):
                print("Usage: /subgoal remove <N>  — N must be a number.")
                return
            except IndexError as e:
                print(f"Index out of range: {e}")
                return
            except RuntimeError:
                print("No active goal. Set one with /goal <text> first.")
                return
            print(f"Removed: {removed}")

        case _:
            criterion = " ".join(args).strip()
            state = manager.add_subgoal(criterion)
            if state is None:
                print("No active goal. Set one with /goal <text> first.")
                return
            print(f"Criterion added ({len(state.subgoals)} total): {criterion}")
            print(manager.status_line())


command = SlashCommandInfo(
    name='subgoal',
    description='Manage subgoals for the active goal. Usage: /subgoal <text> | status | remove <N> | clear',
    handler=_handle_subgoal,
)

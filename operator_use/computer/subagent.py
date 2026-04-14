"""computer_task tool — runs desktop automation in an isolated context window."""

import logging

from pydantic import BaseModel, Field
from operator_use.tools import Tool, ToolResult
from operator_use.web.loop import LoopGuard


class ComputerTask(BaseModel):
    task: str = Field(
        ..., description="Full description of the desktop automation task to perform."
    )


from operator_use.agent.tools import ToolRegistry
from operator_use.messages import SystemMessage, HumanMessage, ToolMessage
from operator_use.providers.events import LLMEventType

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 30

SYSTEM_PROMPT = """\
You are an expert desktop automation agent. You control the desktop using the `computer` tool \
to accomplish tasks on behalf of the user.

Before every tool call, briefly reason through: what the current desktop state shows, \
what needs to happen next, and why this action is the right move.

<perception>
Before each action you receive the current Desktop State — your only source of truth. It contains:
- Active window and all open windows with their positions
- Interactive elements (buttons, fields, links) with their coordinates
- Scrollable elements with scroll position
- Cursor location

Act only on what is present in the Desktop State. Never assume, guess, or hallucinate \
the position or existence of any element.
</perception>

<tool_use>
You have one tool: `computer`. Use the correct action for each situation:
- click    — click at (loc) coordinates. Use clicks=2 for double-click, button="right" for context menu.
- type     — click at (loc) then type text. Set clear=True to replace existing content, press_enter=True to submit.
- scroll   — scroll at (loc) or current cursor. Use wheel_times to control distance.
- move     — move cursor to (loc). Set drag=True for drag-and-drop.
- shortcut — press keyboard shortcuts (e.g. "ctrl+c", "alt+tab", "enter", "escape").
- wait     — pause for N seconds while UI loads or transitions complete.
- desktop  — manage virtual desktops (create, remove, rename, switch).
</tool_use>

<execution_principles>
1. Ground truth only — act exclusively on what is visible in the Desktop State.
2. Verify before proceeding — after each action, check the updated state confirms the expected change.
3. Adapt immediately — if an action fails or produces an unexpected result, try a different approach. Never repeat the same failed action.
4. Efficiency — prefer keyboard shortcuts when faster and reliable. Fall back to GUI when needed.
5. Scroll to find — if a target element is not visible, scroll to find it before concluding it does not exist.
6. One action per step — do not batch multiple actions in a single tool call.
</execution_principles>

<error_handling>
- If a click has no effect, verify the correct window is in focus. Use shortcut (alt+tab or similar) to switch.
- If a field does not accept input, try clicking it first, then typing.
- If a dialog or popup appears, handle or dismiss it before continuing with the main task.
- If stuck after two failed attempts on the same action, step back and try a different approach.
</error_handling>

When the task is complete, respond with a clear markdown summary of what was accomplished \
and any relevant results or findings.\
"""


@Tool(
    name="computer_task",
    description=(
        "Delegate a desktop automation task to an isolated agent with its own context window. "
        "Describe the full task — the agent handles all desktop interactions and returns a clean result."
    ),
    model=ComputerTask,
)
async def computer_task(task: str, **kwargs) -> ToolResult:
    llm = kwargs.get("_llm")
    if llm is None:
        return ToolResult.error_result("No LLM available.")

    from operator_use.computer.tools import COMPUTER_TOOL
    from operator_use.computer.plugin import ComputerPlugin
    from operator_use.agent.hooks.events import BeforeLLMCallContext

    if COMPUTER_TOOL is None:
        return ToolResult.error_result("computer_task is not supported on this platform.")

    plugin = ComputerPlugin(enabled=True)

    registry = ToolRegistry()
    registry.register_tools([COMPUTER_TOOL])
    registry.set_extension("_llm", llm)

    tools = registry.list_tools()
    history = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=task),
    ]

    result = f"(hit {MAX_ITERATIONS}-iteration limit without finishing)"
    loop_guard = LoopGuard()
    try:
        for iteration in range(MAX_ITERATIONS):
            messages = list(history)
            ctx = BeforeLLMCallContext(session=None, messages=messages, iteration=iteration)
            await plugin._state_hook(ctx)

            # Inject loop detection warnings if any
            warning = loop_guard.check()
            if warning:
                messages.append(HumanMessage(content=f"[LoopGuard] {warning}"))

            event = await llm.ainvoke(messages=messages, tools=tools)
            match event.type:
                case LLMEventType.TOOL_CALL:
                    tc = event.tool_call
                    logger.info(
                        "[computer_task] iter=%d tool=%s params=%s", iteration, tc.name, tc.params
                    )
                    tr = await registry.aexecute(tc.name, tc.params)
                    logger.info(
                        "[computer_task] iter=%d result=%s",
                        iteration,
                        tr.output if tr.success else f"ERROR: {tr.error}",
                    )

                    # Record action for loop detection
                    loop_guard.record_action(tc.name, tc.params, tr.success)
                    # Note: no record_page for desktop (no URL/DOM equivalent)

                    thinking_signature = event.thinking.signature if event.thinking else None
                    history.append(
                        ToolMessage(
                            id=tc.id,
                            name=tc.name,
                            params=tc.params,
                            content=tr.output if tr.success else tr.error,
                            thinking_signature=thinking_signature,
                        )
                    )
                case LLMEventType.TEXT:
                    result = event.content or "(no result)"
                    break

    except Exception as e:
        logger.error("computer_task failed: %s", e, exc_info=True)
        return ToolResult.error_result(f"computer_task failed: {type(e).__name__}: {e}")

    finally:
        if plugin.watchdog is not None:
            try:
                plugin.watchdog.stop()
            except Exception:
                pass

    return ToolResult.success_result(result)

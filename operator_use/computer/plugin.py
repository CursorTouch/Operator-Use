"""ComputerPlugin: desktop automation tools + lifecycle hooks."""

import asyncio
import logging
import sys
from typing import TYPE_CHECKING

from operator_use.plugins.base import Plugin
from operator_use.agent.hooks.events import HookEvent

if TYPE_CHECKING:
    from operator_use.agent.hooks import Hooks
    from operator_use.agent.hooks.events import BeforeLLMCallContext, AfterToolCallContext
    from operator_use.agent.tools import ToolRegistry
    from operator_use.agent.context import Context

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
## Desktop Automation

Use the `computer_task` tool to perform desktop interactions. Describe the full task clearly, \
and the tool will run an isolated automation agent with its own context window (30-iteration budget). \
The agent returns a summary of what was accomplished.

<perception>
Before each desktop interaction, the current state of the desktop (active window, visible elements, \
accessibility tree) is injected automatically into your context so you always have an up-to-date \
view of the screen.
</perception>

<tool_use>
Use `computer_task` to delegate desktop goals. Describe the full goal in natural language — \
the desktop subagent handles window focus, clicking, typing, and screen reading internally.
</tool_use>

<execution_principles>
- One `computer_task` call per distinct goal. Chain calls for multi-step workflows.
- Prefer specific, outcome-oriented descriptions: "Save the document as report.docx" not "press Ctrl+S".
- If a task fails, inspect the returned error and retry with a clearer description.
</execution_principles>

Example: "Open Notepad, type 'Hello World', save it as test.txt"

The tool handles all the details of desktop state observation and action execution. You can chain \
multiple `computer_task` calls for different goals (e.g., open an app, then take a screenshot).\
"""


class ComputerPlugin(Plugin):
    """Contributes desktop automation tools and injects desktop state before each LLM call."""

    name = "computer_use"

    def __init__(self, enabled: bool = False):
        self._registry: "ToolRegistry | None" = None
        self._hooks: "Hooks | None" = None
        self._context: "Context | None" = None
        self.desktop = None
        self.watchdog = None
        self._enabled = enabled
        if enabled:
            self._init_sync()

    # ------------------------------------------------------------------
    # Plugin interface
    # ------------------------------------------------------------------

    def get_tools(self) -> list:
        from operator_use.computer.subagent import computer_task

        return [computer_task]

    def get_system_prompt(self) -> str | None:
        return SYSTEM_PROMPT if self._enabled else None

    def register_tools(self, registry: "ToolRegistry") -> None:
        self._registry = registry
        if self._enabled:
            for tool in self.get_tools():
                registry.register(tool)

    def unregister_tools(self, registry: "ToolRegistry") -> None:
        for tool in self.get_tools():
            if registry.get(tool.name) is not None:
                registry.unregister(tool.name)

    def register_hooks(self, hooks: "Hooks") -> None:
        self._hooks = hooks
        if self._enabled:
            hooks.register(HookEvent.BEFORE_LLM_CALL, self._state_hook)
            hooks.register(HookEvent.AFTER_TOOL_CALL, self._wait_for_ui_hook)

    def unregister_hooks(self, hooks: "Hooks") -> None:
        hooks.unregister(HookEvent.BEFORE_LLM_CALL, self._state_hook)
        hooks.unregister(HookEvent.AFTER_TOOL_CALL, self._wait_for_ui_hook)

    def attach_prompt(self, context: "Context") -> None:
        self._context = context
        if self._enabled:
            context.register_plugin_prompt(SYSTEM_PROMPT)

    def detach_prompt(self, context: "Context") -> None:
        if self._context is not None:
            self._context.unregister_plugin_prompt(SYSTEM_PROMPT)

    # ------------------------------------------------------------------
    # Enable / disable
    # ------------------------------------------------------------------

    def _init_sync(self) -> None:
        """Synchronously initialise Desktop and WatchDog (safe to call at startup)."""
        if sys.platform == "win32":
            from operator_use.computer.windows.desktop.service import Desktop
            from operator_use.computer.windows.watchdog.service import WatchDog

            if self.desktop is None:
                self.desktop = Desktop(
                    use_vision=False, use_annotation=False, use_accessibility=True
                )
            if self.watchdog is None:
                self.watchdog = WatchDog()
                self.watchdog.start()
        elif sys.platform == "darwin":
            from operator_use.computer.macos.desktop.service import Desktop
            from operator_use.computer.macos.watchdog.service import WatchDog

            if self.desktop is None:
                self.desktop = Desktop()
            if self.watchdog is None:
                self.watchdog = WatchDog()
                self.watchdog.start()

    async def enable(self) -> None:
        """Dynamically enable computer_use at runtime."""
        self._enabled = True
        if self._hooks is not None:
            self._hooks.register(HookEvent.BEFORE_LLM_CALL, self._state_hook)
            self._hooks.register(HookEvent.AFTER_TOOL_CALL, self._wait_for_ui_hook)
        if self._registry is not None:
            for tool in self.get_tools():
                if self._registry.get(tool.name) is None:
                    self._registry.register(tool)
        if self._context is not None:
            self._context.register_plugin_prompt(SYSTEM_PROMPT)
        logger.info("computer_use enabled")

    async def disable(self) -> None:
        """Dynamically disable computer_use at runtime."""
        self._enabled = False
        if self._hooks is not None:
            self.unregister_hooks(self._hooks)
        if self._registry is not None:
            self.unregister_tools(self._registry)
        if self._context is not None:
            self._context.unregister_plugin_prompt(SYSTEM_PROMPT)
        logger.info("computer_use disabled")

    # ------------------------------------------------------------------
    # Hook handlers
    # ------------------------------------------------------------------

    async def _state_hook(self, ctx: "BeforeLLMCallContext") -> "BeforeLLMCallContext":
        from operator_use.messages import HumanMessage

        try:
            state = await asyncio.get_event_loop().run_in_executor(None, self.desktop.get_state)
            if state:
                ctx.messages.append(HumanMessage(content=state.to_string()))
        except Exception as e:
            logger.debug("Desktop state capture failed: %s", e)
        return ctx

    async def _wait_for_ui_hook(self, ctx: "AfterToolCallContext") -> "AfterToolCallContext":
        if self.watchdog is None:
            await asyncio.sleep(0.5)
            return ctx
        timeout = 1.5
        quiet_window = 0.3
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            self.watchdog.ui_changed.clear()
            await asyncio.sleep(quiet_window)
            if not self.watchdog.ui_changed.is_set():
                break
        return ctx

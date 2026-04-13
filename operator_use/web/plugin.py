"""BrowserPlugin: browser automation tools + state hook."""

import logging
from typing import TYPE_CHECKING

from operator_use.plugins.base import Plugin
from operator_use.agent.hooks.events import HookEvent

if TYPE_CHECKING:
    from operator_use.agent.hooks import Hooks
    from operator_use.agent.hooks.events import BeforeLLMCallContext
    from operator_use.agent.tools import ToolRegistry
    from operator_use.agent.context import Context

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
## Browser Automation

Use the `browser_task` tool to perform web browsing tasks. Describe the full task clearly, \
and the tool will run an isolated browser agent with its own context window (30-iteration budget). \
The agent returns a summary of what was accomplished.

<perception>
Before each browser interaction, current browser state (URL, visible elements, page title) is \
injected automatically into your context so you always have an up-to-date view of the page.
</perception>

<tool_use>
Use `browser_task` to delegate browsing goals. Describe the full goal in natural language — \
the browser subagent handles navigation, clicking, typing, and scraping internally.
</tool_use>

<execution_principles>
- One `browser_task` call per distinct goal. Chain calls for multi-step workflows.
- Prefer specific, outcome-oriented descriptions: "Find the price of X on Y" not "go to Y".
- If a task fails, inspect the returned error and retry with a clearer description.
</execution_principles>

**Setup:**

Start Chrome with remote debugging enabled:
1. Close all Chrome windows
2. Create profile directory (first time only):
   - `mkdir %LOCALAPPDATA%/Operator/chrome-debug-profile`
3. Start Chrome:
   - `chrome.exe --remote-debugging-port=9222 --user-data-dir=%LOCALAPPDATA%/Operator/chrome-debug-profile`
4. Sign into your accounts (Gmail, YouTube, etc.) - logins persist
5. Use browser_task - agent attaches directly to your Chrome with full access

**Example:**
"Go to Gmail and check my inbox for messages from alice@example.com"\
"""


class BrowserPlugin(Plugin):
    """Contributes browser automation tools and injects browser state before each LLM call."""

    name = "browser_use"

    def __init__(self, enabled: bool = False):
        self._registry: "ToolRegistry | None" = None
        self._hooks: "Hooks | None" = None
        self._context: "Context | None" = None
        self.browser = None
        self._enabled = enabled
        if enabled:
            self._init_sync()

    # ------------------------------------------------------------------
    # Plugin interface
    # ------------------------------------------------------------------

    def get_tools(self) -> list:
        from operator_use.web.subagent import browser_task

        return [browser_task]

    def get_system_prompt(self) -> str | None:
        return SYSTEM_PROMPT if self._enabled else None

    def register_tools(self, registry: "ToolRegistry") -> None:
        self._registry = registry
        if self._enabled:
            if self.browser is not None:
                registry.set_extension("browser", self.browser)
                registry.set_extension("_browser", self.browser)
            for tool in self.get_tools():
                registry.register(tool)

    def unregister_tools(self, registry: "ToolRegistry") -> None:
        registry.unset_extension("browser")
        registry.unset_extension("_browser")
        for tool in self.get_tools():
            if registry.get(tool.name) is not None:
                registry.unregister(tool.name)

    def register_hooks(self, hooks: "Hooks") -> None:
        self._hooks = hooks
        if self._enabled:
            hooks.register(HookEvent.BEFORE_LLM_CALL, self._state_hook)

    def unregister_hooks(self, hooks: "Hooks") -> None:
        hooks.unregister(HookEvent.BEFORE_LLM_CALL, self._state_hook)

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
        """Synchronously initialise Browser (safe to call at startup)."""
        from operator_use.web.browser.service import Browser
        from operator_use.web.browser.config import BrowserConfig

        if self.browser is None:
            self.browser = Browser(config=BrowserConfig(use_system_profile=True))

    async def enable(self) -> None:
        """Dynamically enable browser_use at runtime."""
        self._enabled = True
        if self._hooks is not None:
            self._hooks.register(HookEvent.BEFORE_LLM_CALL, self._state_hook)
        if self._registry is not None:
            if self.browser is not None:
                self._registry.set_extension("browser", self.browser)
                self._registry.set_extension("_browser", self.browser)
            for tool in self.get_tools():
                if self._registry.get(tool.name) is None:
                    self._registry.register(tool)
        if self._context is not None:
            self._context.register_plugin_prompt(SYSTEM_PROMPT)
        logger.info("browser_use enabled")

    async def disable(self) -> None:
        """Dynamically disable browser_use at runtime."""
        self._enabled = False
        if self._hooks is not None:
            self.unregister_hooks(self._hooks)
        if self._registry is not None:
            self.unregister_tools(self._registry)
        if self._context is not None:
            self._context.unregister_plugin_prompt(SYSTEM_PROMPT)
        logger.info("browser_use disabled")

    # ------------------------------------------------------------------
    # Hook handlers
    # ------------------------------------------------------------------

    async def _state_hook(self, ctx: "BeforeLLMCallContext") -> "BeforeLLMCallContext":
        from operator_use.messages import HumanMessage

        try:
            if self.browser._client is None:
                return ctx
            if self.browser._get_current_session_id() is None:
                return ctx
            state = await self.browser.get_state()
            if state:
                state_str = state.to_string()
                ctx.messages.append(HumanMessage(content=state_str))
        except Exception as e:
            logger.debug("Browser state capture failed: %s", e)
        return ctx

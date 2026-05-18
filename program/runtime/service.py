from __future__ import annotations

from pathlib import Path
from typing import Any

from program.runtime.types import RuntimeConfig, RuntimeContext
from program.agent.service import Agent
from program.agent.types import PromptOptions
from program.cron.types import CronJob
from program.commands.registry import CommandRegistry
from program.commands.types import parse_command
from program.extension.types import (
    SessionStartEvent, SessionShutdownEvent, SessionBeforeSwitchEvent,
    SessionBeforeSwitchResult, SessionBeforeForkEvent, SessionBeforeForkResult,
)


class Runtime:
    """
    Orchestrates the full session lifecycle: creation, switching, forking,
    and slash-command dispatch on top of Agent / RuntimeContext.

    Usage:
        runtime = await Runtime.create(config)
        await runtime.user_input("/compact shrink the history")
        await runtime.user_input("explain this code")
    """

    def __init__(
        self,
        context: RuntimeContext,
        config: RuntimeConfig,
    ) -> None:
        self._context = context
        self._config = config
        self.commands = CommandRegistry(
            runtime=self,
            discovered=self._context.resource_loader.get_commands(),
        )
        self.commands.register_from_extensions(
            self._context.extension_runtime.get_commands()
        )
        # Give the agent a back-reference so ctx.new_session() / fork() / switch_session() work
        if context.agent is not None:
            context.agent._runtime = self

        # Wire cron callback and start the scheduler
        if context.cron is not None:
            context.cron.on_job = self._handle_cron_job
            context.cron.start()

    # -------------------------------------------------------------------------
    # Factory
    # -------------------------------------------------------------------------

    @classmethod
    async def create(
        cls,
        config: RuntimeConfig,
    ) -> Runtime:
        context = await RuntimeContext.create(config)
        runtime = cls(context=context, config=config)
        await runtime._emit_session_start('startup')
        return runtime

    # -------------------------------------------------------------------------
    # Public properties
    # -------------------------------------------------------------------------

    @property
    def current_session(self) -> Agent | None:
        return self._context.agent

    @property
    def session_manager(self):
        return self._context.session_manager

    # -------------------------------------------------------------------------
    # Core input entry point
    # -------------------------------------------------------------------------

    async def user_input(self, text: str, options: PromptOptions | None = None) -> None:
        """
        Route user input. Slash commands go to CommandRegistry;
        everything else is forwarded to the active Agent.
        """
        parsed = parse_command(text)
        if parsed is not None:
            await self.commands.dispatch(parsed)
        else:
            await self.invoke(text, options)

    async def invoke(self, user_input: str, options: PromptOptions | None = None) -> None:
        """Forward a plain prompt to the current session."""
        if self._context.agent is None:
            raise RuntimeError("No active session available.")
        await self._context.agent.invoke(user_input, options)

    # -------------------------------------------------------------------------
    # Session lifecycle
    # -------------------------------------------------------------------------

    async def reload(self) -> None:
        """
        Reload all resources (tools, skills, commands, extensions, hooks)
        without touching the active session. The conversation history is
        preserved; only the runtime environment is refreshed.
        """
        resource_loader = self._context.resource_loader
        await resource_loader.reload()

        # Re-register file-based hooks (clear old registrations first).
        hooks = self._context.hooks
        hooks.clear()
        for event_type, handler in resource_loader.get_hooks():
            hooks.register(event_type, handler)

        # Rebuild the ExtensionRuntime with newly discovered extensions.
        from program.extension.runtime import ExtensionRuntime
        load_result = resource_loader.get_extensions()
        new_ext = ExtensionRuntime(load_result, self._context.agent, hooks=hooks)
        self._context.extension_runtime = new_ext
        if self._context.agent is not None:
            self._context.agent._extensions = new_ext

        # Swap the engine's tool list to pick up added/removed builtin tools.
        engine = self._context.engine
        new_tools = resource_loader.get_tools() + self._config.tools
        engine.tools = new_tools
        engine._tools = {t.name: t for t in new_tools}

        # Rebuild the command registry with the reloaded commands.
        self.commands = CommandRegistry(
            runtime=self,
            discovered=resource_loader.get_commands(),
        )
        self.commands.register_from_extensions(new_ext.get_commands())

    async def new_session(self) -> None:
        """Shut down the current session and start a fresh one."""
        await self._emit_session_shutdown('new')
        self._config = self._config.model_copy(update={'session_file': None})
        self._context = await RuntimeContext.create(self._config)
        self.commands = CommandRegistry(
            runtime=self,
            discovered=self._context.resource_loader.get_commands(),
        )
        self.commands.register_from_extensions(
            self._context.extension_runtime.get_commands()
        )
        if self._context.agent is not None:
            self._context.agent._runtime = self
        await self._emit_session_start('new')

    async def resume_session(self, session_file: Path) -> None:
        """Shut down the current session and resume an existing one from a file."""
        before_results = await self._context.extension_runtime.emit(
            'session_before_switch',
            SessionBeforeSwitchEvent(reason='resume', target_session_file=str(session_file)),
        )
        for r in before_results:
            if isinstance(r, SessionBeforeSwitchResult) and r.cancel:
                return

        await self._emit_session_shutdown('resume')
        self._config = self._config.model_copy(update={'session_file': session_file})
        self._context = await RuntimeContext.create(self._config)
        self.commands = CommandRegistry(
            runtime=self,
            discovered=self._context.resource_loader.get_commands(),
        )
        self.commands.register_from_extensions(
            self._context.extension_runtime.get_commands()
        )
        if self._context.agent is not None:
            self._context.agent._runtime = self
        await self._emit_session_start('resume')

    async def fork_session(self, from_entry_id: str) -> None:
        """Branch the session tree at the given entry and start a new leaf."""
        sm = self._context.session_manager
        if from_entry_id not in sm.by_id:
            raise KeyError(f"Entry '{from_entry_id}' not found in session.")

        before_results = await self._context.extension_runtime.emit(
            'session_before_fork',
            SessionBeforeForkEvent(entry_id=from_entry_id),
        )
        for r in before_results:
            if isinstance(r, SessionBeforeForkResult) and r.cancel:
                return

        sm.branch(from_entry_id)
        await self._emit_session_start('fork')

    def shutdown(self) -> None:
        """Stop background services (cron). Call when exiting the REPL."""
        if self._context.cron is not None:
            self._context.cron.stop()

    # -------------------------------------------------------------------------
    # Cron
    # -------------------------------------------------------------------------

    async def _handle_cron_job(self, job: CronJob) -> None:
        """Inject a cron job's message into the agent as a prompt."""
        import logging
        logger = logging.getLogger(__name__)
        logger.info('Cron job invoking agent | id=%s name=%s', job.id, job.name)
        await self.invoke(job.payload.message, PromptOptions(source='cron'))

    # -------------------------------------------------------------------------
    # Extension event helpers
    # -------------------------------------------------------------------------

    async def _emit_session_start(self, reason: str) -> None:
        await self._context.extension_runtime.emit(
            'session_start',
            SessionStartEvent(reason=reason),  # type: ignore[arg-type]
        )

    async def _emit_session_shutdown(self, reason: str) -> None:
        await self._context.extension_runtime.emit(
            'session_shutdown',
            SessionShutdownEvent(reason=reason),  # type: ignore[arg-type]
        )

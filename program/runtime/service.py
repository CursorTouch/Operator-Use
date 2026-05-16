from __future__ import annotations

from pathlib import Path
from typing import Any

from program.runtime.loader import RuntimeLoader
from program.runtime.types import RuntimeConfig
from program.agent.service import Agent
from program.agent.types import PromptOptions
from program.commands.registry import CommandRegistry
from program.commands.types import parse_command
from program.extension.types import (
    SessionStartEvent, SessionShutdownEvent, SessionBeforeSwitchEvent,
    SessionBeforeSwitchResult, SessionBeforeForkEvent, SessionBeforeForkResult,
)


class Runtime:
    """
    Orchestrates the full session lifecycle: creation, switching, forking,
    and slash-command dispatch on top of Agent / RuntimeLoader.

    Usage:
        runtime = await Runtime.create(config)
        await runtime.handle_input("/compact shrink the history")
        await runtime.handle_input("explain this code")
    """

    def __init__(
        self,
        services: RuntimeLoader,
        config: RuntimeConfig,
    ) -> None:
        self._services = services
        self._config = config
        self.commands = CommandRegistry(runtime=self)
        self.commands.register_from_extensions(
            self._services.extension_runtime.get_commands()
        )

    # -------------------------------------------------------------------------
    # Factory
    # -------------------------------------------------------------------------

    @classmethod
    async def create(
        cls,
        config: RuntimeConfig,
    ) -> Runtime:
        services = await RuntimeLoader.create(config)
        runtime = cls(services=services, config=config)
        await runtime._emit_session_start('startup')
        return runtime

    # -------------------------------------------------------------------------
    # Public properties
    # -------------------------------------------------------------------------

    @property
    def current_session(self) -> Agent | None:
        return self._services.agent

    @property
    def session_manager(self):
        return self._services.session_manager

    # -------------------------------------------------------------------------
    # Core input entry point
    # -------------------------------------------------------------------------

    async def handle_input(self, text: str, options: PromptOptions | None = None) -> None:
        """
        Route user input. Slash commands go to CommandRegistry;
        everything else is forwarded to the active Agent.
        """
        parsed = parse_command(text)
        if parsed is not None:
            await self.commands.dispatch(parsed)
        else:
            await self.prompt(text, options)

    async def prompt(self, user_input: str, options: PromptOptions | None = None) -> None:
        """Forward a plain prompt to the current session."""
        if self._services.agent is None:
            raise RuntimeError("No active session.")
        await self._services.agent.prompt(user_input, options)

    # -------------------------------------------------------------------------
    # Session lifecycle
    # -------------------------------------------------------------------------

    async def new_session(self) -> None:
        """Shut down the current session and start a fresh one."""
        await self._emit_session_shutdown('new')
        self._config = self._config.model_copy(update={'session_file': None})
        self._services = await RuntimeLoader.create(self._config)
        self.commands = CommandRegistry(runtime=self)
        self.commands.register_from_extensions(
            self._services.extension_runtime.get_commands()
        )
        await self._emit_session_start('new')

    async def resume_session(self, session_file: Path) -> None:
        """Shut down the current session and resume an existing one from a file."""
        before_results = await self._services.extension_runtime.emit(
            'session_before_switch',
            SessionBeforeSwitchEvent(reason='resume', target_session_file=str(session_file)),
        )
        for r in before_results:
            if isinstance(r, SessionBeforeSwitchResult) and r.cancel:
                return

        await self._emit_session_shutdown('resume')
        self._config = self._config.model_copy(update={'session_file': session_file})
        self._services = await RuntimeLoader.create(self._config)
        self.commands = CommandRegistry(runtime=self)
        self.commands.register_from_extensions(
            self._services.extension_runtime.get_commands()
        )
        await self._emit_session_start('resume')

    async def fork_session(self, from_entry_id: str) -> None:
        """Branch the session tree at the given entry and start a new leaf."""
        sm = self._services.session_manager
        if from_entry_id not in sm.by_id:
            raise KeyError(f"Entry '{from_entry_id}' not found in session.")

        before_results = await self._services.extension_runtime.emit(
            'session_before_fork',
            SessionBeforeForkEvent(entry_id=from_entry_id),
        )
        for r in before_results:
            if isinstance(r, SessionBeforeForkResult) and r.cancel:
                return

        sm.branch(from_entry_id)
        await self._emit_session_start('fork')

    # -------------------------------------------------------------------------
    # Extension event helpers
    # -------------------------------------------------------------------------

    async def _emit_session_start(self, reason: str) -> None:
        await self._services.extension_runtime.emit(
            'session_start',
            SessionStartEvent(reason=reason),  # type: ignore[arg-type]
        )

    async def _emit_session_shutdown(self, reason: str) -> None:
        await self._services.extension_runtime.emit(
            'session_shutdown',
            SessionShutdownEvent(reason=reason),  # type: ignore[arg-type]
        )

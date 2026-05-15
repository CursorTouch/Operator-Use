from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from program.commands.builtins import BUILTIN_COMMANDS
from program.commands.types import SlashCommandInfo, CommandParseResult

if TYPE_CHECKING:
    from program.agent_session.runtime import AgentSessionRuntime


class CommandRegistry:
    """
    Holds all registered slash commands and dispatches parsed input.
    Attach to AgentSessionRuntime via `runtime` so handlers can call back.
    """

    def __init__(self, runtime: AgentSessionRuntime | None = None) -> None:
        self.runtime = runtime
        self._commands: dict[str, SlashCommandInfo] = {}
        self._register_builtins()

    def _register_builtins(self) -> None:
        for cmd in BUILTIN_COMMANDS:
            self.register(cmd)

    def register(self, command: SlashCommandInfo) -> None:
        """Register a command and all its aliases."""
        self._commands[command.name] = command
        for alias in command.aliases:
            self._commands[alias] = command

    def register_from_extensions(self, ext_commands: dict[str, Any]) -> None:
        """Register commands discovered from loaded extensions."""
        for name, registered in ext_commands.items():
            cmd = SlashCommandInfo(
                name=name,
                description=registered.description or '',
                handler=lambda reg, args, h=registered.handler: h(reg, args),
            )
            self.register(cmd)

    def get(self, name: str) -> SlashCommandInfo | None:
        return self._commands.get(name)

    def list(self) -> list[SlashCommandInfo]:
        """Return unique commands (aliases deduplicated)."""
        seen: set[str] = set()
        result: list[SlashCommandInfo] = []
        for cmd in self._commands.values():
            if cmd.name not in seen:
                seen.add(cmd.name)
                result.append(cmd)
        return result

    async def dispatch(self, parsed: CommandParseResult) -> bool:
        """
        Dispatch a parsed command. Returns True if the command was found,
        False if unknown.
        """
        cmd = self._commands.get(parsed.name)
        if cmd is None:
            print(f"Unknown command: /{parsed.name}. Type /help for a list of commands.")
            return False
        result = cmd.handler(self, parsed.args)
        if asyncio.iscoroutine(result):
            await result
        return True

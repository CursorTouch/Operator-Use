from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from operator_use.commands.types import SlashCommandInfo, CommandParseResult

if TYPE_CHECKING:
    from operator_use.runtime.service import Runtime


class CommandRegistry:
    """
    Holds all registered slash commands and dispatches parsed input.
    Attach to Runtime via `runtime` so handlers can call back.
    """

    def __init__(self, runtime: Runtime | None = None, discovered: list[SlashCommandInfo] | None = None) -> None:
        self.runtime = runtime
        self._commands: dict[str, SlashCommandInfo] = {}
        for cmd in (discovered if discovered is not None else self.from_builtins()):
            self.register(cmd)

    @staticmethod
    def from_builtins() -> list[SlashCommandInfo]:
        from operator_use.commands.loader import load_commands_from_dir
        from operator_use.settings.paths import get_builtins_commands_dir
        return load_commands_from_dir(get_builtins_commands_dir()).commands

    def register(self, command: SlashCommandInfo) -> None:
        """Register a command and its aliases in the registry."""
        self._commands[command.name] = command
        for alias in command.aliases:
            self._commands[alias] = command

    def register_from_extensions(self, ext_commands: dict[str, Any]) -> None:
        """Register commands provided by extensions."""
        for name, registered in ext_commands.items():
            cmd = SlashCommandInfo(
                name=name,
                description=registered.description or '',
                handler=lambda reg, args, h=registered.handler: h(reg, args),
            )
            self.register(cmd)

    def get(self, name: str) -> SlashCommandInfo | None:
        """Look up a command by name or alias, returning None if not found."""
        return self._commands.get(name)

    def list(self) -> list[SlashCommandInfo]:
        """Return all unique registered commands (deduplicating aliases)."""
        seen: set[str] = set()
        result: list[SlashCommandInfo] = []
        for cmd in self._commands.values():
            if cmd.name not in seen:
                seen.add(cmd.name)
                result.append(cmd)
        return result

    async def dispatch(self, parsed: CommandParseResult) -> bool:
        """Find and invoke a command handler; return True if dispatched, False if not found."""
        cmd = self._commands.get(parsed.name)
        if cmd is None:
            print(f"Unknown command: /{parsed.name}. Type /help for a list of commands.")
            return False
        result = cmd.handler(self, parsed.args)
        if asyncio.iscoroutine(result):
            await result
        return True

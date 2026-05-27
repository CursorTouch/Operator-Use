from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from operator_use.commands.registry import CommandRegistry


@dataclass
class SlashCommandInfo:
    name: str                           # e.g. "compact", "clear", "new"
    description: str
    handler: Callable[['CommandRegistry', list[str]], Awaitable[None] | None]
    aliases: list[str] = field(default_factory=list)


@dataclass
class CommandParseResult:
    name: str
    args: list[str]
    raw: str


def parse_command(text: str) -> CommandParseResult | None:
    """Return a CommandParseResult if text starts with '/', else None."""
    stripped = text.strip()
    if not stripped.startswith('/'):
        return None
    parts = stripped[1:].split()
    if not parts:
        return None
    return CommandParseResult(name=parts[0].lower(), args=parts[1:], raw=stripped)

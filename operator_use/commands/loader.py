from __future__ import annotations

import importlib.util
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from operator_use.commands.types import SlashCommandInfo


@dataclass
class CommandError:
    path: str
    error: str
    stack: str = ""


@dataclass
class LoadCommandsResult:
    commands: list[SlashCommandInfo] = field(default_factory=list)
    errors: list[CommandError] = field(default_factory=list)


def load_command_from_file(path: Path) -> tuple[list[SlashCommandInfo], list[CommandError]]:
    errors: list[CommandError] = []
    str_path = str(path)

    try:
        spec = importlib.util.spec_from_file_location(f"_cmd_{path.stem}", path)
        if spec is None or spec.loader is None:
            errors.append(CommandError(path=str_path, error=f"Cannot create module spec for {path}"))
            return [], errors

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        loaded: list[SlashCommandInfo] = []

        if hasattr(module, "commands"):
            value = module.commands
            if callable(value):
                value = value()
            if isinstance(value, list):
                loaded.extend(c for c in value if isinstance(c, SlashCommandInfo))
        elif hasattr(module, "command"):
            value = module.command
            if callable(value) and not isinstance(value, SlashCommandInfo):
                value = value()
            if isinstance(value, SlashCommandInfo):
                loaded.append(value)

        if not loaded:
            errors.append(CommandError(
                path=str_path,
                error='Command file must export a SlashCommandInfo as "command" or a list as "commands"',
            ))

        return loaded, errors

    except Exception:
        errors.append(CommandError(
            path=str_path,
            error=traceback.format_exc().strip().splitlines()[-1],
            stack=traceback.format_exc(),
        ))
        return [], errors


def load_commands_from_dir(directory: Path) -> LoadCommandsResult:
    commands: list[SlashCommandInfo] = []
    errors: list[CommandError] = []

    if not directory.is_dir():
        return LoadCommandsResult(commands=commands, errors=errors)

    for file in sorted(directory.glob("*.py")):
        if file.name.startswith("_"):
            continue
        file_commands, file_errors = load_command_from_file(file)
        commands.extend(file_commands)
        errors.extend(file_errors)

    return LoadCommandsResult(commands=commands, errors=errors)


def load_commands(dirs: list[Path]) -> LoadCommandsResult:
    """Load commands from multiple directories, deduplicating by name (first wins)."""
    seen: set[str] = set()
    all_commands: list[SlashCommandInfo] = []
    all_errors: list[CommandError] = []

    for directory in dirs:
        result = load_commands_from_dir(directory)
        all_errors.extend(result.errors)
        for cmd in result.commands:
            if cmd.name not in seen:
                seen.add(cmd.name)
                all_commands.append(cmd)

    return LoadCommandsResult(commands=all_commands, errors=all_errors)

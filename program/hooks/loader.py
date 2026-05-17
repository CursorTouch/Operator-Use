from __future__ import annotations

import importlib.util
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

HookRegistration = tuple[str, Callable]


@dataclass
class HookError:
    path: str
    error: str
    stack: str = ""


@dataclass
class LoadHooksResult:
    hooks: list[HookRegistration] = field(default_factory=list)
    errors: list[HookError] = field(default_factory=list)


def load_hooks_from_file(path: Path) -> tuple[list[HookRegistration], list[HookError]]:
    errors: list[HookError] = []
    str_path = str(path)

    try:
        spec = importlib.util.spec_from_file_location(f"_hook_{path.stem}", path)
        if spec is None or spec.loader is None:
            errors.append(HookError(path=str_path, error=f"Cannot create module spec for {path}"))
            return [], errors

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        loaded: list[HookRegistration] = []

        if hasattr(module, "hooks"):
            value = module.hooks
            if callable(value):
                value = value()
            if isinstance(value, list):
                for item in value:
                    if (
                        isinstance(item, (tuple, list))
                        and len(item) == 2
                        and isinstance(item[0], str)
                        and callable(item[1])
                    ):
                        loaded.append((item[0], item[1]))

        if not loaded:
            errors.append(HookError(
                path=str_path,
                error='Hook file must export a list of (event_type, handler) tuples as "hooks"',
            ))

        return loaded, errors

    except Exception:
        errors.append(HookError(
            path=str_path,
            error=traceback.format_exc().strip().splitlines()[-1],
            stack=traceback.format_exc(),
        ))
        return [], errors


def load_hooks_from_dir(directory: Path) -> LoadHooksResult:
    hooks: list[HookRegistration] = []
    errors: list[HookError] = []

    if not directory.is_dir():
        return LoadHooksResult(hooks=hooks, errors=errors)

    for file in sorted(directory.glob("*.py")):
        if file.name.startswith("_"):
            continue
        file_hooks, file_errors = load_hooks_from_file(file)
        hooks.extend(file_hooks)
        errors.extend(file_errors)

    return LoadHooksResult(hooks=hooks, errors=errors)


def load_hooks(dirs: list[Path]) -> LoadHooksResult:
    all_hooks: list[HookRegistration] = []
    all_errors: list[HookError] = []

    for directory in dirs:
        result = load_hooks_from_dir(directory)
        all_hooks.extend(result.hooks)
        all_errors.extend(result.errors)

    return LoadHooksResult(hooks=all_hooks, errors=all_errors)

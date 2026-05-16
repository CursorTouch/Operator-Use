from __future__ import annotations

import importlib.util
import traceback
from pathlib import Path

from program.tool.types import Tool, ToolError, LoadToolsResult


def load_tool_from_file(path: Path) -> tuple[list[Tool], list[ToolError]]:
    """
    Load Tool instances from a single Python file.

    The file must export one of:
      - `tool`: a Tool instance or a callable returning one
      - `tools`: a list of Tool instances
    """
    errors: list[ToolError] = []
    str_path = str(path)

    try:
        spec = importlib.util.spec_from_file_location(f"_tool_{path.stem}", path)
        if spec is None or spec.loader is None:
            errors.append(ToolError(path=str_path, error=f'Cannot create module spec for {path}'))
            return [], errors

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        # Accept either a single `tool` export or a `tools` list
        loaded: list[Tool] = []

        if hasattr(module, 'tools'):
            value = module.tools
            if callable(value):
                value = value()
            if isinstance(value, list):
                loaded.extend(t for t in value if isinstance(t, Tool))
        elif hasattr(module, 'tool'):
            value = module.tool
            if callable(value) and not isinstance(value, Tool):
                value = value()
            if isinstance(value, Tool):
                loaded.append(value)

        if not loaded:
            errors.append(ToolError(
                path=str_path,
                error='Tool file must export a Tool instance as "tool" or a list as "tools"',
            ))

        return loaded, errors

    except Exception:
        errors.append(ToolError(
            path=str_path,
            error=traceback.format_exc().strip().splitlines()[-1],
            stack=traceback.format_exc(),
        ))
        return [], errors


def load_tools_from_dir(directory: Path) -> LoadToolsResult:
    """Discover and load all *.py tool files in a directory."""
    tools: list[Tool] = []
    errors: list[ToolError] = []

    if not directory.is_dir():
        return LoadToolsResult(tools=tools, errors=errors)

    for file in sorted(directory.glob('*.py')):
        if file.name.startswith('_'):
            continue
        file_tools, file_errors = load_tool_from_file(file)
        tools.extend(file_tools)
        errors.extend(file_errors)

    return LoadToolsResult(tools=tools, errors=errors)


def load_tools(dirs: list[Path]) -> LoadToolsResult:
    """Load tools from multiple directories, deduplicating by name (first wins)."""
    seen: set[str] = set()
    all_tools: list[Tool] = []
    all_errors: list[ToolError] = []

    for directory in dirs:
        result = load_tools_from_dir(directory)
        all_errors.extend(result.errors)
        for tool in result.tools:
            if tool.name not in seen:
                seen.add(tool.name)
                all_tools.append(tool)

    return LoadToolsResult(tools=all_tools, errors=all_errors)

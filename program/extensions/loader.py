"""
Extension loader — discovers, loads, and wires Python extension modules.

Convention: each extension file must expose a callable named ``setup``
(sync or async) that accepts an :class:`ExtensionAPI` instance::

    async def setup(pi: ExtensionAPI) -> None:
        pi.on("session_start", my_handler)
        pi.register_tool(my_tool)

Discovery order (mirrors loader.ts):
  1. Local:    <cwd>/.program/extensions/
  2. Global:   ~/.program/agent/extensions/
  3. Explicit paths from settings
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import logging
import os
import re
from typing import Any, Callable, Optional, Union

from program.extensions.types import (
    Extension,
    ExtensionAPI,
    ExtensionFactory,
    ExtensionFlag,
    ExtensionRuntime,
    LoadExtensionsResult,
    RegisteredCommand,
    RegisteredTool,
    ToolDefinition,
    ToolInfo,
)
from program.settings.paths import CONFIG_DIR_NAME, CONFIG_DIR_PATH

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

# Matches non-breaking and other Unicode whitespace variants.
# TS source: /[  -   　]/g
_UNICODE_SPACES = re.compile(
    "[  -   　]"
)


def _normalize_unicode_spaces(s: str) -> str:
    return _UNICODE_SPACES.sub(" ", s)


def _expand_path(p: str) -> str:
    return os.path.expanduser(_normalize_unicode_spaces(p))


def _resolve_path(ext_path: str, cwd: str) -> str:
    expanded = _expand_path(ext_path)
    if os.path.isabs(expanded):
        return expanded
    return os.path.normpath(os.path.join(cwd, expanded))


def _is_extension_file(name: str) -> bool:
    return name.endswith(".py") and name != "__init__.py"


# ---------------------------------------------------------------------------
# Manifest  (program.json  ≡  package.json { "pi": { … } })
# ---------------------------------------------------------------------------

def _read_program_manifest(directory: str) -> Optional[dict]:
    """Read program.json from *directory*, return its contents or None."""
    try:
        with open(os.path.join(directory, "program.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _resolve_extension_entries(directory: str) -> Optional[list[str]]:
    """
    Return resolved entry-point file paths for *directory*, or None.

    Priority:
    1. program.json ``extensions`` list
    2. __init__.py
    """
    manifest = _read_program_manifest(directory)
    if manifest and manifest.get("extensions"):
        entries = [
            full
            for rel in manifest["extensions"]
            if os.path.isfile(full := os.path.normpath(os.path.join(directory, rel)))
        ]
        if entries:
            return entries

    init = os.path.join(directory, "__init__.py")
    return [init] if os.path.isfile(init) else None


def _discover_extensions_in_dir(directory: str) -> list[str]:
    """
    Discover extension files one level deep inside *directory*.

    Rules (mirrors discoverExtensionsInDir in loader.ts):
    - Direct .py files (excluding __init__.py) → load
    - Sub-directories with __init__.py or program.json → load their entries
    """
    if not os.path.isdir(directory):
        return []

    discovered: list[str] = []
    try:
        for name in sorted(os.listdir(directory)):
            entry_path = os.path.join(directory, name)

            # Direct file
            if os.path.isfile(entry_path) and _is_extension_file(name):
                discovered.append(entry_path)
                continue

            # Sub-directory (or symlink to one)
            if os.path.isdir(entry_path):
                entries = _resolve_extension_entries(entry_path)
                if entries:
                    discovered.extend(entries)
    except OSError:
        pass

    return discovered


# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------

# Monotonically increasing counter so every load gets a unique module name
# even when the same path is loaded twice (e.g. after a reload).
_load_counter = 0


def _load_module_from_file(file_path: str) -> Any:
    """
    Import *file_path* as an isolated module with no sys.modules caching.
    Equivalent to jiti with moduleCache: false.
    """
    global _load_counter
    _load_counter += 1
    module_name = f"_program_ext_{_load_counter}"

    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _extract_factory(module: Any) -> Optional[ExtensionFactory]:
    """Return the ``setup`` callable exported by *module*, or None."""
    factory = getattr(module, "setup", None)
    return factory if callable(factory) else None


# ---------------------------------------------------------------------------
# Runtime creation
# ---------------------------------------------------------------------------

def _not_initialized(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError(
        "Extension runtime not initialized. "
        "Action methods cannot be called during extension loading."
    )


def create_extension_runtime() -> ExtensionRuntime:
    """
    Create a runtime whose action stubs raise until :meth:`ExtensionRunner.bind_core`
    wires real implementations (mirrors createExtensionRuntime in loader.ts).
    """
    runtime = ExtensionRuntime()

    # These all raise — extensions must not call actions during loading.
    runtime.send_message = _not_initialized
    runtime.send_user_message = _not_initialized
    runtime.append_entry = _not_initialized
    runtime.set_session_name = _not_initialized
    runtime.get_session_name = _not_initialized
    runtime.set_label = _not_initialized
    runtime.get_active_tools = _not_initialized
    runtime.get_all_tools = _not_initialized
    runtime.set_active_tools = _not_initialized
    runtime.get_commands = _not_initialized
    runtime.set_model = _not_initialized
    runtime.get_thinking_level = _not_initialized
    runtime.set_thinking_level = _not_initialized

    # refresh_tools is safe to call during loading (registerTool is valid then).
    runtime.refresh_tools = lambda: None

    return runtime


# ---------------------------------------------------------------------------
# Extension object construction
# ---------------------------------------------------------------------------

def _create_extension(extension_path: str, resolved_path: str) -> Extension:
    is_inline = extension_path.startswith("<") and extension_path.endswith(">")
    source = (
        extension_path[1:-1].split(":")[0] or "temporary"
        if is_inline
        else "local"
    )
    base_dir = None if is_inline else os.path.dirname(resolved_path)
    source_info = {"source": source, "base_dir": base_dir, "path": extension_path}

    return Extension(
        path=extension_path,
        resolved_path=resolved_path,
        source_info=source_info,
    )


# ---------------------------------------------------------------------------
# Concrete ExtensionAPI
# ---------------------------------------------------------------------------

class ConcreteExtensionAPI(ExtensionAPI):
    """
    ExtensionAPI bound to a single :class:`Extension` and shared runtime.

    - Registration methods write into ``extension.*``.
    - Action methods guard with ``assert_active()`` then delegate to the runtime.
    """

    def __init__(self, extension: Extension, runtime: ExtensionRuntime, cwd: str) -> None:
        self._ext = extension
        self._runtime = runtime
        self._cwd = cwd

    # -- Event subscription --------------------------------------------------

    def on(self, event: str, handler: Callable) -> None:
        self._runtime.assert_active()
        self._ext.handlers.setdefault(event, []).append(handler)

    # -- Tool registration ---------------------------------------------------

    def register_tool(self, tool: ToolDefinition) -> None:
        self._runtime.assert_active()
        self._ext.tools[tool.name] = RegisteredTool(
            definition=tool,
            source_info=self._ext.source_info,
        )
        self._runtime.refresh_tools()

    # -- Command / shortcut / flag registration ------------------------------

    def register_command(self, name: str, options: dict) -> None:
        self._runtime.assert_active()
        self._ext.commands[name] = RegisteredCommand(
            name=name,
            handler=options["handler"],
            source_info=self._ext.source_info,
            description=options.get("description"),
            get_argument_completions=options.get("get_argument_completions"),
        )

    def register_shortcut(self, shortcut: Any, options: dict) -> None:
        pass  # UI shortcuts not supported in headless mode

    def register_flag(self, name: str, options: dict) -> None:
        self._runtime.assert_active()
        self._ext.flags[name] = ExtensionFlag(
            name=name,
            type=options["type"],
            extension_path=self._ext.path,
            description=options.get("description"),
            default=options.get("default"),
        )
        default = options.get("default")
        if default is not None and name not in self._runtime.flag_values:
            self._runtime.flag_values[name] = default

    def get_flag(self, name: str) -> Optional[Union[bool, str]]:
        self._runtime.assert_active()
        if name not in self._ext.flags:
            return None
        return self._runtime.flag_values.get(name)

    def register_message_renderer(self, custom_type: str, renderer: Any) -> None:
        pass  # Message renderers not supported in headless mode

    # -- Action methods (delegate to runtime) --------------------------------

    def send_message(self, message: dict, options: Optional[dict] = None) -> None:
        self._runtime.assert_active()
        self._runtime.send_message(message, options)

    def send_user_message(self, content: Union[str, list], options: Optional[dict] = None) -> None:
        self._runtime.assert_active()
        self._runtime.send_user_message(content, options)

    def append_entry(self, custom_type: str, data: Any = None) -> None:
        self._runtime.assert_active()
        self._runtime.append_entry(custom_type, data)

    def set_session_name(self, name: str) -> None:
        self._runtime.assert_active()
        self._runtime.set_session_name(name)

    def get_session_name(self) -> Optional[str]:
        self._runtime.assert_active()
        return self._runtime.get_session_name()

    def set_label(self, entry_id: str, label: Optional[str]) -> None:
        self._runtime.assert_active()
        self._runtime.set_label(entry_id, label)

    async def exec(self, command: str, args: list[str], options: Optional[dict] = None) -> Any:
        self._runtime.assert_active()
        cwd = (options or {}).get("cwd", self._cwd)
        proc = await asyncio.create_subprocess_exec(
            command,
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return {
            "exit_code": proc.returncode,
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
        }

    def get_active_tools(self) -> list[str]:
        self._runtime.assert_active()
        return self._runtime.get_active_tools()

    def get_all_tools(self) -> list[ToolInfo]:
        self._runtime.assert_active()
        return self._runtime.get_all_tools()

    def set_active_tools(self, tool_names: list[str]) -> None:
        self._runtime.assert_active()
        self._runtime.set_active_tools(tool_names)

    def get_commands(self) -> list[Any]:
        self._runtime.assert_active()
        return self._runtime.get_commands()

    async def set_model(self, model: Any) -> bool:
        self._runtime.assert_active()
        result = self._runtime.set_model(model)
        if inspect.isawaitable(result):
            return bool(await result)
        return bool(result)

    def get_thinking_level(self) -> Any:
        self._runtime.assert_active()
        return self._runtime.get_thinking_level()

    def set_thinking_level(self, level: Any) -> None:
        self._runtime.assert_active()
        self._runtime.set_thinking_level(level)

    def register_provider(self, name: str, options: dict) -> None:
        self._runtime.assert_active()
        self._runtime.register_provider(name, options, self._ext.path)

    def unregister_provider(self, name: str) -> None:
        self._runtime.assert_active()
        self._runtime.unregister_provider(name, self._ext.path)


# ---------------------------------------------------------------------------
# Single-extension loading
# ---------------------------------------------------------------------------

async def _load_extension(
    extension_path: str,
    cwd: str,
    runtime: ExtensionRuntime,
) -> tuple[Optional[Extension], Optional[str]]:
    resolved_path = _resolve_path(extension_path, cwd)
    try:
        module = _load_module_from_file(resolved_path)
        if module is None:
            return None, f"Could not load module: {extension_path}"

        factory = _extract_factory(module)
        if factory is None:
            return None, f"Extension does not export a 'setup' function: {extension_path}"

        extension = _create_extension(extension_path, resolved_path)
        api = ConcreteExtensionAPI(extension, runtime, cwd)

        if asyncio.iscoroutinefunction(factory):
            await factory(api)
        else:
            factory(api)

        return extension, None
    except Exception as exc:
        return None, f"Failed to load extension {extension_path}: {exc}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def load_extension_from_factory(
    factory: ExtensionFactory,
    cwd: str,
    runtime: ExtensionRuntime,
    extension_path: str = "<inline>",
) -> Extension:
    """Load an extension from an in-process factory callable."""
    extension = _create_extension(extension_path, extension_path)
    api = ConcreteExtensionAPI(extension, runtime, cwd)
    if asyncio.iscoroutinefunction(factory):
        await factory(api)
    else:
        factory(api)
    return extension


async def load_extensions(
    paths: list[str],
    cwd: str,
) -> LoadExtensionsResult:
    """Load extensions from an explicit list of file paths."""
    runtime = create_extension_runtime()
    extensions: list[Extension] = []
    errors: list[dict] = []

    for ext_path in paths:
        extension, error = await _load_extension(ext_path, cwd, runtime)
        if error:
            errors.append({"path": ext_path, "error": error})
        elif extension:
            extensions.append(extension)

    return LoadExtensionsResult(extensions=extensions, errors=errors, runtime=runtime)


async def discover_and_load_extensions(
    configured_paths: list[str],
    cwd: str,
    agent_dir: Optional[str] = None,
) -> LoadExtensionsResult:
    """
    Discover and load extensions from standard locations plus explicit paths.

    Discovery order:
    1. Local:    ``<cwd>/.program/extensions/``
    2. Global:   ``~/.program/agent/extensions/``  (or *agent_dir*/extensions/)
    3. Explicit  *configured_paths*

    Paths are deduplicated by their resolved absolute form so the same file is
    never loaded twice regardless of how it was discovered.
    """
    if agent_dir is None:
        agent_dir = str(CONFIG_DIR_PATH / "agent")

    all_paths: list[str] = []
    seen: set[str] = set()

    def _add(paths: list[str]) -> None:
        for p in paths:
            key = os.path.abspath(p)
            if key not in seen:
                seen.add(key)
                all_paths.append(p)

    # 1. Project-local
    _add(_discover_extensions_in_dir(os.path.join(cwd, CONFIG_DIR_NAME, "extensions")))

    # 2. Global
    _add(_discover_extensions_in_dir(os.path.join(agent_dir, "extensions")))

    # 3. Explicit configured paths
    for p in configured_paths:
        resolved = _resolve_path(p, cwd)
        if os.path.isdir(resolved):
            entries = _resolve_extension_entries(resolved)
            if entries:
                _add(entries)
            else:
                _add(_discover_extensions_in_dir(resolved))
        else:
            _add([resolved])

    return await load_extensions(all_paths, cwd)

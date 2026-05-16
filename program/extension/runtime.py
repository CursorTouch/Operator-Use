from __future__ import annotations

import asyncio
import inspect
import traceback
from typing import Any

from program.extension.types import (
    Extension, ExtensionContext, ExtensionError, ExtensionEvent,
    LoadExtensionsResult,
)
from program.hooks.service import Hooks


class ExtensionRuntime:
    """
    Dispatches lifecycle events to all loaded extensions and the system Hooks registry.

    Usage:
        runtime = ExtensionRuntime(load_result, context, hooks)
        await runtime.emit('session_start', event)
    """

    def __init__(
        self,
        load_result: LoadExtensionsResult,
        context: ExtensionContext,
        hooks: Hooks | None = None,
    ) -> None:
        self._extensions = load_result.extensions
        self._ctx = context
        self._hooks = hooks or Hooks()
        self._errors: list[ExtensionError] = list(load_result.errors)

    @property
    def errors(self) -> list[ExtensionError]:
        return self._errors

    async def emit(self, event_type: str, event: Any) -> list[Any]:
        """
        Emit an event to all extensions and the system Hooks registry.
        Returns a list of non-None results from handlers.
        """
        results: list[Any] = []

        for ext in self._extensions:
            handlers = ext.handlers.get(event_type, [])
            for handler in handlers:
                try:
                    result = handler(event, self._ctx)
                    if inspect.isawaitable(result):
                        result = await result
                    if result is not None:
                        results.append(result)
                except Exception:
                    self._errors.append(ExtensionError(
                        extension_path=ext.path,
                        event=event_type,
                        error=traceback.format_exc().strip().splitlines()[-1],
                        stack=traceback.format_exc(),
                    ))

        hook_results = await self._hooks.emit(event)
        results.extend(r for r in hook_results if r is not None)

        return results

    async def emit_parallel(self, event_type: str, event: Any) -> list[Any]:
        """
        Emit an event to all extensions and Hooks concurrently.
        Use for fire-and-forget events where order doesn't matter.
        """
        tasks = []

        for ext in self._extensions:
            for handler in ext.handlers.get(event_type, []):
                async def _run(h=handler, e=ext):
                    try:
                        result = h(event, self._ctx)
                        if inspect.isawaitable(result):
                            result = await result
                        return result
                    except Exception:
                        self._errors.append(ExtensionError(
                            extension_path=e.path,
                            event=event_type,
                            error=traceback.format_exc().strip().splitlines()[-1],
                            stack=traceback.format_exc(),
                        ))
                        return None

                tasks.append(_run())

        tasks.append(self._hooks.emit(event))

        all_results = await asyncio.gather(*tasks)
        results = []
        for r in all_results:
            if isinstance(r, list):
                results.extend(x for x in r if x is not None)
            elif r is not None:
                results.append(r)
        return results

    def has_handlers(self, event_type: str) -> bool:
        """Return True if any loaded extension has at least one handler for event_type."""
        return any(event_type in ext.handlers and ext.handlers[event_type] for ext in self._extensions)

    def get_tools(self) -> dict[str, Any]:
        """Collect all registered tools from all extensions (last-writer-wins on name)."""
        tools = {}
        for ext in self._extensions:
            tools.update(ext.tools)
        return tools

    def get_commands(self) -> dict[str, Any]:
        """Collect all registered commands from all extensions."""
        commands = {}
        for ext in self._extensions:
            commands.update(ext.commands)
        return commands

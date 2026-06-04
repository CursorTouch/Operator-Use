from __future__ import annotations

import asyncio
import inspect
import traceback
from typing import Any

from operator_use.extension.types import (
    Extension, ExtensionContext, ExtensionError, ExtensionEvent,
    LoadExtensionsResult,
)
from operator_use.hooks.service import Hooks


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
        """Return all accumulated extension loading and execution errors."""
        return self._errors

    async def emit(self, event_type: str, event: Any) -> list[Any]:
        """Emit an event to all extensions and the system Hooks registry.

        Returns a list of non-None results from handlers. Extension handler exceptions
        are caught, logged, and do not abort processing.

        Args:
            event_type: The event type identifier (e.g., 'session_start').
            event: The event object to dispatch (should have a .type attribute).

        Returns:
            List of non-None results from all matching handlers.
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

        if hasattr(event, 'type'):
            hook_results = await self._hooks.emit(event)
            results.extend(r for r in hook_results if r is not None)

        return results

    async def emit_parallel(self, event_type: str, event: Any) -> list[Any]:
        """Emit an event to all extensions and Hooks concurrently.

        Use for fire-and-forget events where order doesn't matter. All handlers
        run in parallel via asyncio.gather. Exceptions are caught per-handler.

        Args:
            event_type: The event type identifier.
            event: The event object to dispatch.

        Returns:
            List of non-None results from all matching handlers.
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

        if hasattr(event, 'type'):
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

    def get_guardrails(self) -> dict[str, Any]:
        """Collect all registered guardrails from all extensions (last-writer-wins on name)."""
        guardrails = {}
        for ext in self._extensions:
            guardrails.update(ext.guardrails)
        return guardrails

    def get_commands(self) -> dict[str, Any]:
        """Collect all registered commands from all extensions."""
        commands = {}
        for ext in self._extensions:
            commands.update(ext.commands)
        return commands

    def get_providers(self) -> list[Any]:
        """Collect all custom text inference providers registered by extensions."""
        return [rp.provider for ext in self._extensions for rp in ext.inference_providers]

    def get_image_providers(self) -> list[Any]:
        """Collect all custom image providers registered by extensions."""
        return [rp.provider for ext in self._extensions for rp in ext.image_providers]

    def get_audio_providers(self) -> list[Any]:
        """Collect all custom audio providers registered by extensions."""
        return [rp.provider for ext in self._extensions for rp in ext.audio_providers]

    def get_video_providers(self) -> list[Any]:
        """Collect all custom video providers registered by extensions."""
        return [rp.provider for ext in self._extensions for rp in ext.video_providers]

    def get_text_apis(self) -> dict[str, Any]:
        """Collect all custom text LLM API classes registered by extensions (last-writer-wins)."""
        apis: dict[str, Any] = {}
        for ext in self._extensions:
            for name, ra in ext.inference_apis.items():
                apis[name] = ra.api
        return apis

    def get_image_apis(self) -> dict[str, Any]:
        """Collect all custom image API classes registered by extensions (last-writer-wins)."""
        apis: dict[str, Any] = {}
        for ext in self._extensions:
            for name, ra in ext.image_apis.items():
                apis[name] = ra.api
        return apis

    def get_audio_apis(self) -> dict[str, Any]:
        """Collect all custom audio API classes registered by extensions (last-writer-wins)."""
        apis: dict[str, Any] = {}
        for ext in self._extensions:
            for name, ra in ext.audio_apis.items():
                apis[name] = ra.api
        return apis

    def get_video_apis(self) -> dict[str, Any]:
        """Collect all custom video API classes registered by extensions (last-writer-wins)."""
        apis: dict[str, Any] = {}
        for ext in self._extensions:
            for name, ra in ext.video_apis.items():
                apis[name] = ra.api
        return apis

    def get_memory_providers(self) -> list[Any]:
        """Collect all custom memory providers registered by extensions."""
        return [rmp.provider for ext in self._extensions for rmp in ext.memory_providers]

    def get_memory_apis(self) -> dict[str, Any]:
        """Collect all custom memory API classes registered by extensions (last-writer-wins)."""
        apis: dict[str, Any] = {}
        for ext in self._extensions:
            for name, rma in ext.memory_apis.items():
                apis[name] = rma.api
        return apis

    def get_subagent_profiles(self) -> list[Any]:
        """Collect all subagent profiles registered by extensions."""
        return [rsp.profile for ext in self._extensions for rsp in ext.subagent_profiles]

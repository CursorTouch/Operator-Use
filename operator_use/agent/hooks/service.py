"""Hooks: register and emit lifecycle hooks."""

import logging
from collections import defaultdict
from typing import Callable, Awaitable, TypeVar

from operator_use.agent.hooks.events import HookEvent

logger = logging.getLogger(__name__)

HookContext = TypeVar("HookContext")
HookHandler = Callable[[HookContext], Awaitable[None]]


class Hooks:
    """Stores and fires async hook handlers keyed by HookEvent."""

    def __init__(self):
        self._handlers: dict[HookEvent, list[HookHandler]] = defaultdict(list)

    def register(self, event: HookEvent, handler: HookHandler) -> None:
        """Register an async handler for a lifecycle event."""
        self._handlers[event].append(handler)

    def unregister(self, event: HookEvent, handler: HookHandler) -> None:
        """Remove a previously registered handler. No-op if not found."""
        try:
            self._handlers[event].remove(handler)
        except ValueError:
            pass

    def on(self, event: HookEvent):
        """Decorator form: @hooks.on(HookEvent.BEFORE_TOOL_CALL)"""

        def decorator(fn: HookHandler) -> HookHandler:
            self.register(event, fn)
            return fn

        return decorator

    async def emit(self, event: HookEvent, context: HookContext) -> HookContext:
        """Fire all handlers for the event, passing the context object.

        Handlers may mutate the context (e.g. set skip=True, modify response).
        Exceptions in handlers are logged but do not abort the pipeline.
        """
        for handler in self._handlers[event]:
            try:
                await handler(context)
            except Exception as e:
                logger.error("Hook handler %s raised: %s", handler.__name__, e, exc_info=True)
        return context

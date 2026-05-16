from __future__ import annotations

import asyncio
import traceback
from collections import defaultdict
from typing import Any, Callable


Handler = Callable[[Any], Any]
Unsubscribe = Callable[[], None]


class EventBus:
    """
    Pub/sub bus for extension-to-extension communication.

    Extensions access this via api.events:
        api.events.on("my-channel", handler)
        api.events.emit("my-channel", data)
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def on(self, channel: str, handler: Handler) -> Unsubscribe:
        """Subscribe to a channel. Returns an unsubscribe function."""
        self._handlers[channel].append(handler)

        def unsubscribe() -> None:
            try:
                self._handlers[channel].remove(handler)
            except ValueError:
                pass

        return unsubscribe

    def once(self, channel: str, handler: Handler) -> Unsubscribe:
        """Subscribe for a single emission, then auto-unsubscribe."""
        def wrapper(data: Any) -> Any:
            unsub()
            return handler(data)
        unsub = self.on(channel, wrapper)
        return unsub

    def emit(self, channel: str, data: Any = None) -> None:
        """Publish data to all subscribers on a channel (fire-and-forget for async handlers)."""
        for handler in list(self._handlers.get(channel, [])):
            try:
                result = handler(data)
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)
            except Exception:
                print(f"[EventBus] handler error on channel '{channel}':\n{traceback.format_exc()}")

    async def emit_async(self, channel: str, data: Any = None) -> None:
        """Publish data, awaiting async handlers sequentially before returning."""
        for handler in list(self._handlers.get(channel, [])):
            try:
                result = handler(data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                print(f"[EventBus] handler error on channel '{channel}':\n{traceback.format_exc()}")

    def channels(self) -> list[str]:
        """Return channels that currently have at least one active subscriber."""
        return [ch for ch, handlers in self._handlers.items() if handlers]

    def subscriber_count(self, channel: str) -> int:
        """Return the number of active subscribers on a channel."""
        return len(self._handlers.get(channel, []))

    def clear(self) -> None:
        """Remove all subscriptions."""
        self._handlers.clear()

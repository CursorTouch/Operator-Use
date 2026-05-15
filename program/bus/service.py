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

    def emit(self, channel: str, data: Any = None) -> None:
        """Publish data to all subscribers on a channel."""
        for handler in list(self._handlers.get(channel, [])):
            try:
                result = handler(data)
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)
            except Exception:
                print(f"[EventBus] handler error on channel '{channel}':\n{traceback.format_exc()}")

    def clear(self) -> None:
        """Remove all subscriptions."""
        self._handlers.clear()

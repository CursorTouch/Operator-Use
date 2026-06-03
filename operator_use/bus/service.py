from __future__ import annotations
import asyncio
from operator_use.bus.types import IncomingMessage, OutgoingMessage


class Bus:
    """Two-queue message bus decoupling channels from the runtime."""

    def __init__(self) -> None:
        self._incoming: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        self._outgoing: asyncio.Queue[OutgoingMessage] = asyncio.Queue()

    async def publish_incoming(self, msg: IncomingMessage) -> None:
        """Queue a message from a gateway channel."""
        await self._incoming.put(msg)

    async def consume_incoming(self) -> IncomingMessage:
        """Dequeue the next message from a gateway channel."""
        return await self._incoming.get()

    async def publish_outgoing(self, msg: OutgoingMessage) -> None:
        """Queue a message to a gateway channel."""
        await self._outgoing.put(msg)

    async def consume_outgoing(self) -> OutgoingMessage:
        """Dequeue the next message to a gateway channel."""
        return await self._outgoing.get()


# Backward-compat alias used by extension and resource modules.
EventBus = Bus

from __future__ import annotations
import asyncio
from program.bus.types import IncomingMessage, OutgoingMessage


class Bus:
    """Two-queue message bus decoupling channels from the runtime."""

    def __init__(self) -> None:
        self._incoming: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        self._outgoing: asyncio.Queue[OutgoingMessage] = asyncio.Queue()

    async def publish_incoming(self, msg: IncomingMessage) -> None:
        await self._incoming.put(msg)

    async def consume_incoming(self) -> IncomingMessage:
        return await self._incoming.get()

    async def publish_outgoing(self, msg: OutgoingMessage) -> None:
        await self._outgoing.put(msg)

    async def consume_outgoing(self) -> OutgoingMessage:
        return await self._outgoing.get()

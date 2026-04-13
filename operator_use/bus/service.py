"""Bus: async message queue for decoupled channel-agent communication.
Provides publish/consume methods for incoming and outgoing messages."""

import asyncio

from operator_use.bus.views import IncomingMessage, OutgoingMessage


class Bus:
    """Message bus for decentralized communication between agent, heartbeat, and channels."""

    def __init__(self) -> None:
        self._incoming: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        self._outgoing: asyncio.Queue[OutgoingMessage] = asyncio.Queue()

    async def publish_incoming(self, message: IncomingMessage) -> None:
        """Put a message into the incoming queue."""
        await self._incoming.put(message)

    async def consume_incoming(self) -> IncomingMessage:
        """Take the next message from the incoming queue (blocks until available)."""
        return await self._incoming.get()

    async def publish_outgoing(self, message: OutgoingMessage) -> None:
        """Put a message into the outgoing queue."""
        await self._outgoing.put(message)

    async def consume_outgoing(self) -> OutgoingMessage:
        """Take the next message from the outgoing queue (blocks until available)."""
        return await self._outgoing.get()

    @property
    def incoming_size(self) -> int:
        """Get the number of messages in the incoming queue."""
        return self._incoming.qsize()

    @property
    def outgoing_size(self) -> int:
        """Get the number of messages in the outgoing queue."""
        return self._outgoing.qsize()

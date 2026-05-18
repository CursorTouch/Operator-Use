from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from program.bus.service import Bus
    from program.bus.types import IncomingMessage, OutgoingMessage


class BaseChannel(ABC):
    """
    Abstract base for all Gateway channels.

    Lifecycle:
        - Gateway sets channel.bus when registering.
        - connect() establishes the connection (no-op by default).
        - disconnect() tears down the connection.
        - When a user message arrives, channel calls receive() → bus.publish_incoming().
        - Gateway calls send(OutgoingMessage) to deliver responses.
    """

    def __init__(self) -> None:
        self.bus: Bus | None = None

    @property
    @abstractmethod
    def channel_id(self) -> str:
        """Unique identifier for this channel (e.g. 'telegram', 'discord', 'ws:123')."""
        ...

    async def connect(self) -> None:
        """Establish the channel connection. Override in self-starting channels."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect and release resources."""
        ...

    async def receive(self, msg: IncomingMessage) -> None:
        """Push an incoming message onto the bus."""
        if self.bus is not None:
            await self.bus.publish_incoming(msg)

    @abstractmethod
    async def send(self, msg: OutgoingMessage) -> None:
        """Deliver an outgoing message to the user."""
        ...

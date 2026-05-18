from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class GatewayEvent:
    """An event produced by the agent and delivered to a channel."""
    type: Literal['stream_start', 'chunk', 'stream_end', 'tool_start', 'tool_end', 'error', 'done']
    data: dict[str, Any] = field(default_factory=dict)


class BaseChannel(ABC):
    """
    Abstract base for all Gateway channels.

    A channel represents one connected client or transport (e.g. a WebSocket
    connection, a stdio session).

    Lifecycle:
        - start() is called once to begin the receive loop (no-op by default).
        - The Gateway calls on_event() to stream agent events back to the channel.
        - on_event(stream_end) calls self.send() with the accumulated response.
        - The Gateway calls send() directly for out-of-band messages (e.g. subagent results).
    """

    @property
    @abstractmethod
    def channel_id(self) -> str:
        """Unique identifier for this channel instance."""
        ...

    async def start(self) -> None:
        """Start the channel's receive loop. Override in channels that self-manage polling."""
        ...

    @abstractmethod
    async def on_event(self, event: GatewayEvent) -> None:
        """Receive an agent lifecycle event and handle it."""
        ...

    @abstractmethod
    async def send(self, text: str) -> None:
        """Send a plain text message to the user on this channel."""
        ...

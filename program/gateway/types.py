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
    connection, a stdio session). The Gateway calls on_event() to deliver
    events; channel implementations call gateway.send() to submit user input.
    """

    @property
    @abstractmethod
    def channel_id(self) -> str:
        """Unique identifier for this channel instance."""
        ...

    @abstractmethod
    async def on_event(self, event: GatewayEvent) -> None:
        """Receive an agent lifecycle event and handle it."""
        ...

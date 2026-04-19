"""Base channel ABC for all channel implementations."""

import logging

from operator_use.bus.views import OutgoingMessage, IncomingMessage
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from operator_use.bus import Bus

logger = logging.getLogger(__name__)


class BaseChannel(ABC):
    """Abstract base class for message channels (WhatsApp, Slack, Telegram, etc.).

    Gateway sets channel.bus when adding. When the channel receives a message,
    it pushes to bus.publish_incoming(msg). start() takes no args.
    """

    def __init__(self, config: dict, bus: "Bus") -> None:
        self.config = config
        self.bus = bus
        self.running: bool = False

    def _cfg(self, key: str, default=None):
        """Get a config value from the channel's config dataclass."""
        return getattr(self.config, key, default)

    def _is_user_allowed(self, user_id: str) -> bool:
        """Check if a user is permitted by the allow_from list.

        - Empty list -> deny all (default-deny) with a WARNING log.
        - ["*"] -> allow everyone.
        - Otherwise -> allow only listed IDs.
        """
        allow_list = self._cfg("allow_from") or []
        if not allow_list:
            logger.warning(
                "allow_from is empty for %s channel — denying all access. "
                "Set allow_from to a list of user IDs, or [\"*\"] to allow everyone.",
                self.__class__.__name__,
            )
            return False
        if "*" in allow_list:
            return True
        return str(user_id) in [str(x) for x in allow_list]

    @property
    @abstractmethod
    def name(self) -> str:
        """Channel identifier (e.g. 'whatsapp', 'slack', 'telegram')."""
        ...

    @abstractmethod
    async def start(self) -> None:
        """Start the channel. Push received messages to self.bus.publish_incoming(msg)."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel listener."""
        ...

    async def receive(self, message: IncomingMessage) -> None:
        """Receive an incoming message from the channel."""
        if self.bus:
            await self.bus.publish_incoming(message)

    @abstractmethod
    async def _listen(self) -> None:
        """Listen for incoming messages from the channel."""
        ...

    @abstractmethod
    async def send(self, message: OutgoingMessage) -> int | None:
        """Send an outgoing message to the channel. Returns the channel-assigned message_id if available."""
        ...

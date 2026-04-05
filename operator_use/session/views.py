"""Session views."""

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from operator_use.messages.service import BaseMessage

DEFAULT_SESSION_TTL = 3600.0  # 1 hour


@dataclass
class Session:
    """Session data class."""

    id: str
    messages: list[BaseMessage] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    ttl: float = DEFAULT_SESSION_TTL
    _last_activity: float = field(init=False, default_factory=time.monotonic)

    def add_message(self, message: BaseMessage) -> None:
        """Add a message and update updated_at."""
        self.messages.append(message)
        self.updated_at = datetime.now()
        self.touch()

    def get_history(self) -> list[BaseMessage]:
        """Return the message history."""
        return list(self.messages)

    def clear(self) -> None:
        """Clear all messages."""
        self.messages.clear()
        self.updated_at = datetime.now()

    def touch(self) -> None:
        """Refresh last_activity timestamp, extending the session TTL window."""
        self._last_activity = time.monotonic()

    def is_expired(self) -> bool:
        """Return True if idle time since last activity exceeds the TTL."""
        return (time.monotonic() - self._last_activity) > self.ttl

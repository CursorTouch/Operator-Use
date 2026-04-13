"""Session views."""

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from operator_use.messages.service import BaseMessage

if TYPE_CHECKING:
    from operator_use.config.service import Config

DEFAULT_SESSION_TTL = 86400.0  # 24 hours (config-driven default)


@dataclass
class Session:
    """Session data class."""

    id: str
    messages: list[BaseMessage] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    ttl: float = DEFAULT_SESSION_TTL
    # _last_activity is set in __post_init__ so that tests can monkeypatch
    # time.monotonic before instantiation and get a consistent starting value.
    _last_activity: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        self._last_activity = time.monotonic()

    def add_message(self, message: BaseMessage) -> None:
        """Add a message and update updated_at."""
        self.messages.append(message)
        self.updated_at = datetime.now()
        self.touch()

    def get_history(self) -> list[BaseMessage]:
        """Return the message history."""
        return list(self.messages)

    def clear(self) -> None:
        """Clear all messages and refresh the TTL window."""
        self.messages.clear()
        self.updated_at = datetime.now()
        self.touch()

    def touch(self) -> None:
        """Refresh last_activity timestamp, extending the session TTL window."""
        self._last_activity = time.monotonic()

    def is_expired(self) -> bool:
        """Return True if idle time since last activity exceeds the TTL."""
        return (time.monotonic() - self._last_activity) > self.ttl

    @classmethod
    def from_config(cls, id: str, config: "Config") -> "Session":
        """Construct a Session using TTL from config.session.ttl_hours."""
        ttl = config.session.ttl_hours * 3600
        return cls(id=id, ttl=ttl)

    @classmethod
    def _from_persisted(
        cls,
        id: str,
        messages: list[BaseMessage],
        created_at: datetime,
        updated_at: datetime,
        metadata: dict[str, Any],
        ttl: float = DEFAULT_SESSION_TTL,
    ) -> "Session":
        """Reconstruct a Session from disk, anchoring _last_activity to the
        real idle time derived from updated_at so that loaded sessions expire
        correctly rather than resetting to 'now'."""
        session = cls(
            id=id,
            messages=messages,
            created_at=created_at,
            updated_at=updated_at,
            metadata=metadata,
            ttl=ttl,
        )
        idle_seconds = max(0.0, (datetime.now() - updated_at).total_seconds())
        session._last_activity = time.monotonic() - idle_seconds
        return session

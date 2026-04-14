"""Session views."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from operator_use.messages.service import BaseMessage


@dataclass
class Session:
    """Session data class."""

    id: str
    messages: list[BaseMessage] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_message(self, message: BaseMessage) -> None:
        """Add a message and update updated_at."""
        self.messages.append(message)
        self.updated_at = datetime.now()

    def get_history(self) -> list[BaseMessage]:
        """Return the message history."""
        return list(self.messages)

    def clear(self) -> None:
        """Clear all messages."""
        self.messages.clear()
        self.updated_at = datetime.now()

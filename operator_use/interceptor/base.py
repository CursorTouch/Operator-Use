"""Abstract base class for agent restart interceptors."""

from abc import ABC, abstractmethod
from typing import Optional


class Interceptor(ABC):
    """Abstract base class for agent restart interceptors.

    An interceptor observes agent tool calls via BEFORE_TOOL_CALL hooks,
    records state before risky operations, and provides a recovery path when
    a self-improvement restart fails to start the new code.

    Concrete implementations decide *what* to track and *how* to revert.
    """

    @abstractmethod
    def register_history_hook(self, hooks) -> None:
        """Register hooks to observe tool calls before they execute."""
        ...

    @property
    @abstractmethod
    def session_id(self) -> Optional[str]:
        """Active session ID, or None if no session has started."""
        ...

    @abstractmethod
    def reset(self) -> None:
        """Clear the active session (call after a successful restart)."""
        ...

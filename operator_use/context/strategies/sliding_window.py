"""Sliding window strategy: keep only the most recent N non-system messages."""

import logging
from typing import TYPE_CHECKING

from operator_use.context.base import BaseContextStrategy

if TYPE_CHECKING:
    from operator_use.messages import BaseMessage
    from operator_use.providers.base import BaseChatLLM

logger = logging.getLogger(__name__)


class SlidingWindowStrategy(BaseContextStrategy):
    """Trim conversation history to the most recent ``window`` non-system messages.

    Unlike compaction, nothing is summarised — older messages are simply
    dropped. This keeps context lean at the cost of losing early history.

    Args:
        window: Maximum number of non-system messages to retain. Defaults to 20.
    """

    def __init__(self, window: int = 20):
        self.window = window

    async def process(
        self,
        messages: "list[BaseMessage]",
        llm: "BaseChatLLM | None" = None,
    ) -> "list[BaseMessage]":
        from operator_use.messages import SystemMessage

        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        non_system = [m for m in messages if not isinstance(m, SystemMessage)]

        if len(non_system) <= self.window:
            return messages

        trimmed = non_system[-self.window :]
        logger.debug(
            "SlidingWindowStrategy: dropped %d message(s), keeping last %d",
            len(non_system) - self.window,
            self.window,
        )
        return system_msgs + trimmed

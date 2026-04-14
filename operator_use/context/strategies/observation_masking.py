"""Observation masking strategy: blank out old tool outputs, keep recent ones intact."""

import logging
from copy import copy
from typing import TYPE_CHECKING

from operator_use.context.base import BaseContextStrategy

if TYPE_CHECKING:
    from operator_use.messages import BaseMessage
    from operator_use.providers.base import BaseChatLLM

logger = logging.getLogger(__name__)

_MASKED = "[masked — call the tool again if needed]"


class ObservationMaskingStrategy(BaseContextStrategy):
    """Keep all messages but replace tool output content in older turns with a placeholder.

    The most recent ``keep_recent`` non-system messages are left fully intact.
    Everything before that has its ``ToolMessage`` content replaced with
    ``"[masked — call the tool again if needed]"``.

    This gives the LLM a complete picture of *what was done* (tool call sequence,
    arguments, order) without being flooded by large tool outputs from old turns.
    No history is lost and no LLM call is required.

    Args:
        keep_recent: Number of most-recent non-system messages to keep unmasked.
                     Defaults to 10.
    """

    def __init__(self, keep_recent: int = 10):
        self.keep_recent = keep_recent

    async def process(
        self,
        messages: "list[BaseMessage]",
        llm: "BaseChatLLM | None" = None,
    ) -> "list[BaseMessage]":
        from operator_use.messages import SystemMessage, ToolMessage

        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        non_system = [m for m in messages if not isinstance(m, SystemMessage)]

        if len(non_system) <= self.keep_recent:
            return messages

        to_mask = non_system[: -self.keep_recent]
        to_keep = non_system[-self.keep_recent :]

        masked_count = 0
        processed: list = []
        for msg in to_mask:
            if isinstance(msg, ToolMessage) and msg.content != _MASKED:
                msg = copy(msg)
                msg.content = _MASKED
                masked_count += 1
            processed.append(msg)

        if masked_count:
            logger.debug(
                "ObservationMaskingStrategy: masked %d tool message(s), kept last %d messages intact",
                masked_count,
                self.keep_recent,
            )

        return system_msgs + processed + to_keep

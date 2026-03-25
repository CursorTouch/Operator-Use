"""Base class for context management strategies."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from operator_use.messages import BaseMessage
    from operator_use.providers.base import BaseChatLLM


class BaseContextStrategy(ABC):
    """Transforms the message list before each LLM call.

    Subclasses implement different approaches to context management:
    compaction, sliding window, retrieval-augmented trimming, etc.
    """

    @abstractmethod
    async def process(
        self,
        messages: "list[BaseMessage]",
        llm: "BaseChatLLM | None" = None,
    ) -> "list[BaseMessage]":
        """Return a (possibly modified) copy of the message list.

        Args:
            messages: Full message list as built by the agent loop.
            llm: The agent's LLM instance. Strategies that need to call
                 the model for summarisation receive it here.

        Returns:
            The message list to pass to the LLM. May be the same object
            (mutated or untouched) or a new list.
        """
        ...

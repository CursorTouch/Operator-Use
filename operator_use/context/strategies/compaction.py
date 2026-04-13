"""Compaction strategy: summarise history when message count exceeds a threshold."""

import logging
from typing import TYPE_CHECKING

from operator_use.context.base import BaseContextStrategy

if TYPE_CHECKING:
    from operator_use.messages import BaseMessage
    from operator_use.providers.base import BaseChatLLM

logger = logging.getLogger(__name__)

_COMPACTION_PROMPT = """\
Summarise the following conversation history into a concise but complete context \
summary. Preserve all key facts, decisions, tool results, file paths, URLs, and \
any information needed to continue the conversation without losing context.

<conversation>
{conversation}
</conversation>

Respond with only the summary. Do not include any preamble or meta-commentary."""


class CompactionStrategy(BaseContextStrategy):
    """Compress conversation history when non-system messages exceed a threshold.

    When the number of non-system messages reaches ``threshold``, all non-system
    messages are summarised into a single HumanMessage by calling the LLM. The
    history is then replaced with::

        [system_messages..., HumanMessage("[Conversation history compacted]\\n\\n<summary>")]

    Subsequent messages accumulate normally until the threshold is hit again.

    Args:
        threshold: Number of non-system messages that triggers compaction.
                   Defaults to 20.
    """

    def __init__(self, threshold: int = 20):
        self.threshold = threshold

    async def process(
        self,
        messages: "list[BaseMessage]",
        llm: "BaseChatLLM | None" = None,
    ) -> "list[BaseMessage]":
        from operator_use.messages import SystemMessage, HumanMessage
        from operator_use.providers.events import LLMEventType

        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        non_system = [m for m in messages if not isinstance(m, SystemMessage)]

        if len(non_system) < self.threshold:
            return messages

        if llm is None:
            logger.warning(
                "CompactionStrategy: threshold (%d) reached but no LLM available — skipping",
                self.threshold,
            )
            return messages

        conversation_text = "\n\n".join(f"{type(m).__name__}: {m.content}" for m in non_system)
        try:
            event = await llm.ainvoke(
                messages=[
                    HumanMessage(content=_COMPACTION_PROMPT.format(conversation=conversation_text))
                ],
                tools=[],
            )
        except Exception as exc:
            logger.warning("CompactionStrategy: LLM call failed (%s) — skipping compaction", exc)
            return messages

        if event.type != LLMEventType.TEXT or not event.content:
            logger.warning("CompactionStrategy: LLM returned no text — skipping compaction")
            return messages

        summary_msg = HumanMessage(content=f"[Conversation history compacted]\n\n{event.content}")
        compacted = system_msgs + [summary_msg]
        logger.info(
            "CompactionStrategy: compacted %d messages into 1 summary (%d total with system)",
            len(non_system),
            len(compacted),
        )
        return compacted

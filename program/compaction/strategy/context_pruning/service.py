"""Context pruning — in-memory tool result trimmer.

Replaces old, verbose tool results with stubs before each LLM call.
Nothing is written to the session transcript; the full history stays on disk.

Two modes:
  - Soft-trim  : content > soft_trim_chars  → keep head + "..." + tail
  - Hard-clear : content > min_prune_chars  → replace with placeholder text

Tool results in the most recent `protected_tail_tokens` are never touched,
and error results are always left intact.

Wired to engine.options.transform_context at runtime startup.
Runs alongside (not instead of) the main compaction strategy.
"""
from __future__ import annotations

import dataclasses
from typing import Optional, TYPE_CHECKING

from program.compaction.strategy.context_pruning.types import ContextPruningSettings
from program.message.types import BaseMessage, ToolMessage, ToolResultContent

if TYPE_CHECKING:
    from program.engine.types import AbortSignal


# chars-per-token rough estimate used for the protected-tail calculation
_CHARS_PER_TOKEN = 4


class ContextPruner:
    """Wires into engine.options.transform_context to prune old tool results."""

    def __init__(self, settings: ContextPruningSettings) -> None:
        self._settings = settings

    def transform(
        self,
        messages: list[BaseMessage],
        signal: Optional[AbortSignal] = None,
    ) -> list[BaseMessage]:
        """TransformContextCallback — prune old tool results in-memory."""
        s = self._settings
        if not s.enabled:
            return messages

        tail_char_budget = s.protected_tail_tokens * _CHARS_PER_TOKEN

        # Walk from the end to find the protected tail boundary.
        # Count chars in ToolResultContent blocks; stop when the budget is spent.
        tail_chars = 0
        protected: set[int] = set()
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if not isinstance(msg, ToolMessage):
                continue
            msg_chars = sum(
                len(c.content)
                for c in msg.contents
                if isinstance(c, ToolResultContent)
            )
            if tail_chars + msg_chars <= tail_char_budget:
                tail_chars += msg_chars
                protected.add(i)
            else:
                break

        result: list[BaseMessage] = []
        for i, msg in enumerate(messages):
            if not isinstance(msg, ToolMessage) or i in protected:
                result.append(msg)
                continue

            new_contents = []
            changed = False
            for c in msg.contents:
                if not isinstance(c, ToolResultContent) or c.is_error:
                    new_contents.append(c)
                    continue

                length = len(c.content)

                if length <= s.min_prune_chars:
                    # Too short to bother.
                    new_contents.append(c)
                elif length > s.soft_trim_chars:
                    # Soft-trim: preserve head and tail, collapse the middle.
                    head = c.content[: s.soft_trim_head]
                    tail = c.content[-s.soft_trim_tail :]
                    new_contents.append(dataclasses.replace(c, content=f"{head}\n...\n{tail}"))
                    changed = True
                else:
                    # Hard-clear: replace entirely with the placeholder.
                    new_contents.append(dataclasses.replace(c, content=s.placeholder))
                    changed = True

            result.append(
                dataclasses.replace(msg, contents=new_contents) if changed else msg
            )

        return result

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from operator_use.session.types import SessionEntry
    from operator_use.compaction.strategy.types import CompactionPreparation, CompactionResult
    from operator_use.inference.types import ThinkingLevel


class Compaction(ABC):
    """Pluggable context compaction strategy.

    Implement this ABC to replace the built-in summarization-based compaction.
    Pass the instance as ``compaction=`` when constructing ``Agent``.
    """

    @abstractmethod
    def should_compact(self, context_tokens: int, context_window: int) -> bool:
        """Return True if compaction should run after this turn."""

    @abstractmethod
    def prepare(self, path_entries: list[SessionEntry]) -> CompactionPreparation | None:
        """Analyse the session branch and return compaction inputs.

        Return None to skip compaction (e.g. already compacted, no cut point).
        """

    @abstractmethod
    async def compact(
        self,
        preparation: CompactionPreparation,
        custom_instructions: str | None = None,
        thinking_level: ThinkingLevel | None = None,
    ) -> CompactionResult:
        """Execute compaction and return the result to be persisted."""

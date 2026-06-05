from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from operator_use.compaction.strategy.summarization.service import SummarizationCompaction
from operator_use.compaction.strategy.types import (
    CompactionSettings, CompactionPreparation, CompactionResult, CompactionDetails,
)
from operator_use.compaction.strategy.utils import compute_file_lists, format_file_operations
from operator_use.compaction.strategy.observational.pipeline import ObservationPipeline, ObservationPipelineConfig

if TYPE_CHECKING:
    from operator_use.inference.api.text.service import LLM
    from operator_use.inference.types import ThinkingLevel
    from operator_use.session.manager import SessionManager
    from operator_use.session.types import SessionEntry


class ObservationalCompaction(SummarizationCompaction):
    """Summarization compaction augmented with a background observational memory pipeline.

    Inherits all compaction logic from SummarizationCompaction. After each turn the
    caller invokes after_turn(session_manager) — this checks the observation and
    reflection token clocks and spawns background workers when due (non-blocking).

    At compact time, compact() renders the pre-built ledger summary instead of calling
    the LLM summarizer, falling back to parent LLM summarization when no ledger data
    exists yet.
    """

    def __init__(
        self,
        llm: LLM,
        settings: CompactionSettings | None = None,
        settings_provider: Callable[[], CompactionSettings] | None = None,
        obs_config: ObservationPipelineConfig | None = None,
        obs_llm: LLM | None = None,
    ) -> None:
        super().__init__(llm=llm, settings=settings, settings_provider=settings_provider)
        self._obs_pipeline = ObservationPipeline(
            config=obs_config or ObservationPipelineConfig(),
            llm=obs_llm or llm,
        )
        # Cached during prepare() so compact() can access the full branch entries
        self._cached_entries: list[SessionEntry] = []

    @property
    def obs_pipeline(self) -> ObservationPipeline:
        return self._obs_pipeline

    def after_turn(self, session_manager: SessionManager) -> None:
        """Check token clocks and spawn background pipeline if due.

        Returns immediately — all LLM work happens in a daemon thread.
        """
        entries: list[SessionEntry] = session_manager.get_branch()
        if self._obs_pipeline.should_run(entries):
            self._obs_pipeline.spawn_pipeline(session_manager)

    def prepare(self, path_entries: list[SessionEntry]) -> CompactionPreparation | None:
        """Cache entries for use in compact(), then delegate to parent."""
        self._cached_entries = list(path_entries)
        return super().prepare(path_entries)

    async def compact(
        self,
        preparation: CompactionPreparation,
        custom_instructions: str | None = None,
        thinking_level: ThinkingLevel | None = None,
    ) -> CompactionResult:
        """Use pre-built ledger summary if available, otherwise fall back to LLM summarization."""
        # Fold ALL cached entries — observation entries are written after the messages
        # they describe, so they appear past the retained_from_id boundary and would be
        # missed if we stopped there. The ledger represents accumulated session memory
        # and should always be fully included.
        from operator_use.compaction.strategy.observational.ledger import fold_ledger, render_summary
        fold = fold_ledger(self._cached_entries)
        obs_summary = render_summary(fold.active_observations, fold.reflections)
        if obs_summary:
            read_files, modified_files = compute_file_lists(preparation.file_ops)
            summary = obs_summary + format_file_operations(read_files, modified_files)
            return CompactionResult(
                summary=summary,
                retained_from_id=preparation.retained_from_id,
                tokens_before=preparation.tokens_before,
                details=CompactionDetails(read_files=read_files, modified_files=modified_files),
            )
        return await super().compact(preparation, custom_instructions, thinking_level)

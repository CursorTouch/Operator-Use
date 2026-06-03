from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable

from operator_use.inference.types import LLMContext, ThinkingLevel
from operator_use.message.types import UserMessage, TextContent
from operator_use.session.types import SessionEntry, CompactionEntry

from operator_use.compaction.strategy.base import Compaction
from operator_use.compaction.strategy.types import (
    CompactionSettings, CompactionPreparation, CompactionResult, CompactionDetails,
)
from operator_use.compaction.strategy.sliding_window.types import SlidingWindowCompactionSettings
from operator_use.compaction.strategy.summarization.prompts import (
    SUMMARIZATION_SYSTEM_PROMPT, SUMMARIZATION_PROMPT,
    UPDATE_SUMMARIZATION_PROMPT, TURN_PREFIX_SUMMARIZATION_PROMPT,
)
from operator_use.compaction.strategy.utils import (
    estimate_context_tokens, estimate_tokens,
    collect_messages_in_range, find_prev_compaction_index,
    resolve_boundary_start, find_cut_point,
    build_file_ops_from_prev_compaction, accumulate_file_ops,
    compute_file_lists, format_file_operations,
    build_summary_prompt, build_turn_prefix_prompt, extract_text_from_events,
)

if TYPE_CHECKING:
    from operator_use.inference.api.text.service import LLM


class SlidingWindowCompaction(Compaction):
    """Incremental compaction that fires early and compacts in small batches.

    Instead of waiting until the context is nearly full and doing one large
    summarization, SlidingWindowCompaction triggers at ``trigger_percent``
    (default 50%) and compacts only the oldest ``batch_tokens`` worth of turns
    each round. The result is a continuously-updated summary with no abrupt
    large-scale context resets.
    """

    def __init__(
        self,
        llm: LLM,
        settings: SlidingWindowCompactionSettings | None = None,
        settings_provider: Callable[[], SlidingWindowCompactionSettings] | None = None,
    ) -> None:
        self.llm = llm
        self.settings = settings or SlidingWindowCompactionSettings()
        self._settings_provider = settings_provider

    @property
    def _settings(self) -> SlidingWindowCompactionSettings:
        """Resolve settings dynamically if a provider is configured."""
        if self._settings_provider is not None:
            return self._settings_provider()
        return self.settings

    # -------------------------------------------------------------------------
    # Compaction ABC
    # -------------------------------------------------------------------------

    def should_compact(self, context_tokens: int, context_window: int) -> bool:
        """Trigger compaction when context exceeds trigger_percent of window."""
        settings = self._settings
        if not settings.enabled:
            return False
        return context_tokens > context_window * settings.trigger_percent

    def prepare(self, path_entries: list[SessionEntry]) -> CompactionPreparation | None:
        """Prepare a sliding window batch (oldest batch_tokens of content to summarize)."""
        settings = self._settings

        if path_entries and isinstance(path_entries[-1], CompactionEntry):
            return None

        prev_idx = find_prev_compaction_index(path_entries)
        previous_summary = path_entries[prev_idx].summary if prev_idx >= 0 else None  # type: ignore[union-attr]
        boundary_start = resolve_boundary_start(path_entries, prev_idx)
        boundary_end = len(path_entries)

        tokens_before = estimate_context_tokens(
            collect_messages_in_range(path_entries, 0, boundary_end)
        ).tokens

        # Sliding batch: only compact the oldest batch_tokens of content.
        # Compute tokens in the compactable range, then keep all but batch_tokens
        # of it — capped at keep_recent_tokens so the tail is always safe.
        tokens_in_range = estimate_context_tokens(
            collect_messages_in_range(path_entries, boundary_start, boundary_end)
        ).tokens
        sliding_keep = max(
            settings.keep_recent_tokens,
            tokens_in_range - settings.batch_tokens,
        )

        cut = find_cut_point(path_entries, boundary_start, boundary_end, sliding_keep)
        if cut.first_kept_entry_index >= boundary_end:
            return None
        first_kept_entry = path_entries[cut.first_kept_entry_index]
        if not first_kept_entry.id:
            return None

        history_end = cut.turn_start_index if cut.is_split_turn else cut.first_kept_entry_index
        messages_to_summarize = collect_messages_in_range(
            path_entries, boundary_start, history_end, for_compaction=True
        )
        turn_prefix_messages = (
            collect_messages_in_range(
                path_entries, cut.turn_start_index, cut.first_kept_entry_index,
                for_compaction=True,
            )
            if cut.is_split_turn else []
        )

        file_ops = build_file_ops_from_prev_compaction(path_entries, prev_idx)
        accumulate_file_ops(file_ops, messages_to_summarize)
        if cut.is_split_turn:
            accumulate_file_ops(file_ops, turn_prefix_messages)

        return CompactionPreparation(
            retained_from_id=first_kept_entry.id,
            messages_to_summarize=messages_to_summarize,
            turn_prefix_messages=turn_prefix_messages,
            is_split_turn=cut.is_split_turn,
            tokens_before=tokens_before,
            previous_summary=previous_summary,
            file_ops=file_ops,
            settings=CompactionSettings(
                enabled=settings.enabled,
                keep_recent_tokens=settings.keep_recent_tokens,
            ),
        )

    async def compact(
        self,
        preparation: CompactionPreparation,
        custom_instructions: str | None = None,
        thinking_level: ThinkingLevel | None = None,
    ) -> CompactionResult:
        """Summarize the batch, optionally handling split turns separately."""
        if preparation.is_split_turn and preparation.turn_prefix_messages:
            summary = await self._compact_split_turn(preparation, custom_instructions, thinking_level)
        else:
            prompt = build_summary_prompt(
                preparation.messages_to_summarize,
                preparation.previous_summary,
                custom_instructions,
                SUMMARIZATION_PROMPT,
                UPDATE_SUMMARIZATION_PROMPT,
            )
            context = LLMContext(
                messages=[UserMessage(contents=[TextContent(content=prompt)])],
                system_prompt=SUMMARIZATION_SYSTEM_PROMPT,
            )
            events = await self.llm.invoke(context, thinking_level=thinking_level)
            summary = extract_text_from_events(events, "SlidingWindow summarization failed")

        read_files, modified_files = compute_file_lists(preparation.file_ops)
        summary += format_file_operations(read_files, modified_files)

        return CompactionResult(
            summary=summary,
            retained_from_id=preparation.retained_from_id,
            tokens_before=preparation.tokens_before,
            details=CompactionDetails(read_files=read_files, modified_files=modified_files),
        )

    async def _compact_split_turn(
        self,
        preparation: CompactionPreparation,
        custom_instructions: str | None,
        thinking_level: ThinkingLevel | None,
    ) -> str:
        """Handle split turns by summarizing history and turn context separately in parallel."""
        async def summarize_history() -> str:
            prompt = build_summary_prompt(
                preparation.messages_to_summarize,
                preparation.previous_summary,
                custom_instructions,
                SUMMARIZATION_PROMPT,
                UPDATE_SUMMARIZATION_PROMPT,
            )
            context = LLMContext(
                messages=[UserMessage(contents=[TextContent(content=prompt)])],
                system_prompt=SUMMARIZATION_SYSTEM_PROMPT,
            )
            events = await self.llm.invoke(context, thinking_level=thinking_level)
            return extract_text_from_events(events, "SlidingWindow summarization failed")

        async def summarize_turn_prefix() -> str:
            prompt = build_turn_prefix_prompt(
                preparation.turn_prefix_messages, TURN_PREFIX_SUMMARIZATION_PROMPT
            )
            context = LLMContext(
                messages=[UserMessage(contents=[TextContent(content=prompt)])],
                system_prompt=SUMMARIZATION_SYSTEM_PROMPT,
            )
            events = await self.llm.invoke(context, thinking_level=thinking_level)
            return extract_text_from_events(events, "SlidingWindow turn-prefix summarization failed")

        tasks = []
        if preparation.messages_to_summarize:
            tasks.append(summarize_history())
        tasks.append(summarize_turn_prefix())

        results = await asyncio.gather(*tasks)

        if preparation.messages_to_summarize:
            history_result, turn_prefix_result = results
        else:
            history_result = "No prior history."
            turn_prefix_result = results[0]

        return f"{history_result}\n\n---\n\n**Turn Context (split turn):**\n\n{turn_prefix_result}"

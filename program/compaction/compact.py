from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from program.llm.types import LLMContext, TextEndEvent, EndEvent, ErrorEvent, StopReason, ThinkingLevel
from program.message.types import AgentMessage, UserMessage, TextContent
from program.session.types import SessionEntry, CompactionEntry

from program.compaction.types import (
    CompactionSettings, CompactionPreparation, CompactionResult, CompactionDetails,
)
from program.compaction.prompts import (
    SUMMARIZATION_SYSTEM_PROMPT, SUMMARIZATION_PROMPT, UPDATE_SUMMARIZATION_PROMPT,
    TURN_PREFIX_SUMMARIZATION_PROMPT,
)
from program.compaction.utils import (
    estimate_context_tokens, find_cut_point,
    collect_messages_in_range, find_prev_compaction_index,
    resolve_boundary_start, build_file_ops_from_prev_compaction, accumulate_file_ops,
    compute_file_lists, format_file_operations, serialize_conversation,
)

if TYPE_CHECKING:
    from program.llm.service import LLM


class Compaction:
    def __init__(self, llm: LLM, settings: CompactionSettings | None = None):
        self.llm = llm
        self.settings = settings or CompactionSettings()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def should_compact(self, context_tokens: int, context_window: int) -> bool:
        if not self.settings.enabled:
            return False
        return context_tokens > context_window - self.settings.reserve_tokens

    def prepare(self, path_entries: list[SessionEntry]) -> CompactionPreparation | None:
        """
        Analyse path_entries (root → leaf, no header) and return everything
        needed to call compact(). Returns None if the last entry is already a
        compaction or no valid cut point exists.
        """
        if path_entries and isinstance(path_entries[-1], CompactionEntry):
            return None

        prev_idx = find_prev_compaction_index(path_entries)
        previous_summary = path_entries[prev_idx].summary if prev_idx >= 0 else None  # type: ignore[union-attr]
        boundary_start = resolve_boundary_start(path_entries, prev_idx)
        boundary_end = len(path_entries)

        tokens_before = estimate_context_tokens(
            collect_messages_in_range(path_entries, 0, boundary_end)
        ).tokens

        cut = find_cut_point(path_entries, boundary_start, boundary_end, self.settings.keep_recent_tokens)
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
                path_entries, cut.turn_start_index, cut.first_kept_entry_index, for_compaction=True
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
            settings=self.settings,
        )

    async def compact(
        self,
        preparation: CompactionPreparation,
        custom_instructions: str | None = None,
        thinking_level: ThinkingLevel | None = None,
    ) -> CompactionResult:
        if preparation.is_split_turn and preparation.turn_prefix_messages:
            summary = await self._compact_split_turn(preparation, custom_instructions, thinking_level)
        else:
            summary = await self._generate_summary(
                preparation.messages_to_summarize,
                previous_summary=preparation.previous_summary,
                custom_instructions=custom_instructions,
                thinking_level=thinking_level,
            )

        read_files, modified_files = compute_file_lists(preparation.file_ops)
        summary += format_file_operations(read_files, modified_files)

        return CompactionResult(
            summary=summary,
            retained_from_id=preparation.retained_from_id,
            tokens_before=preparation.tokens_before,
            details=CompactionDetails(read_files=read_files, modified_files=modified_files),
        )

    # -------------------------------------------------------------------------
    # Split-turn handling
    # -------------------------------------------------------------------------

    async def _compact_split_turn(
        self,
        preparation: CompactionPreparation,
        custom_instructions: str | None,
        thinking_level: ThinkingLevel | None,
    ) -> str:
        tasks = []
        if preparation.messages_to_summarize:
            tasks.append(self._generate_summary(
                preparation.messages_to_summarize,
                previous_summary=preparation.previous_summary,
                custom_instructions=custom_instructions,
                thinking_level=thinking_level,
            ))
        tasks.append(
            self._generate_turn_prefix_summary(preparation.turn_prefix_messages, thinking_level)
        )

        results = await asyncio.gather(*tasks)

        if preparation.messages_to_summarize:
            history_result, turn_prefix_result = results
        else:
            history_result = "No prior history."
            turn_prefix_result = results[0]

        return f"{history_result}\n\n---\n\n**Turn Context (split turn):**\n\n{turn_prefix_result}"

    # -------------------------------------------------------------------------
    # Prompt builders
    # -------------------------------------------------------------------------

    def _build_summary_prompt(
        self,
        messages: list[AgentMessage],
        previous_summary: str | None,
        custom_instructions: str | None,
    ) -> str:
        base = UPDATE_SUMMARIZATION_PROMPT if previous_summary else SUMMARIZATION_PROMPT
        if custom_instructions:
            base = f"{base}\n\nAdditional focus: {custom_instructions}"
        conversation_text = serialize_conversation(messages)
        prompt = f"<conversation>\n{conversation_text}\n</conversation>\n\n"
        if previous_summary:
            prompt += f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
        return prompt + base

    def _build_turn_prefix_prompt(self, messages: list[AgentMessage]) -> str:
        conversation_text = serialize_conversation(messages)
        return f"<conversation>\n{conversation_text}\n</conversation>\n\n{TURN_PREFIX_SUMMARIZATION_PROMPT}"

    # -------------------------------------------------------------------------
    # LLM invocation
    # -------------------------------------------------------------------------

    async def _invoke_llm(
        self,
        prompt_text: str,
        thinking_level: ThinkingLevel | None,
        error_label: str,
    ) -> str:
        """Send a single-message prompt to the LLM and return the collected text."""
        original_thinking = self.llm.api.options.thinking_level
        if thinking_level and thinking_level != ThinkingLevel.Off:
            self.llm.api.options.thinking_level = thinking_level
        try:
            context = LLMContext(
                messages=[UserMessage(contents=[TextContent(content=prompt_text)])],
                system_prompt=SUMMARIZATION_SYSTEM_PROMPT,
            )
            events = await self.llm.invoke(context)
        finally:
            self.llm.api.options.thinking_level = original_thinking

        return self._collect_text(events, error_label)

    @staticmethod
    def _collect_text(events: list, error_label: str) -> str:
        """Extract text from LLM events, raising on error stop reason."""
        text_parts: list[str] = []
        stop_reason = StopReason.Stop
        error = ""
        for event in events:
            match event:
                case TextEndEvent():
                    text_parts.append(event.text.content)
                case EndEvent():
                    stop_reason = event.reason
                case ErrorEvent():
                    stop_reason = event.reason
                    error = event.error
        if stop_reason == StopReason.Error:
            raise RuntimeError(f"{error_label}: {error or 'Unknown error'}")
        return "".join(text_parts)

    # -------------------------------------------------------------------------
    # Summary generators
    # -------------------------------------------------------------------------

    async def _generate_summary(
        self,
        messages: list[AgentMessage],
        previous_summary: str | None = None,
        custom_instructions: str | None = None,
        thinking_level: ThinkingLevel | None = None,
    ) -> str:
        prompt = self._build_summary_prompt(messages, previous_summary, custom_instructions)
        return await self._invoke_llm(prompt, thinking_level, "Summarization failed")

    async def _generate_turn_prefix_summary(
        self,
        messages: list[AgentMessage],
        thinking_level: ThinkingLevel | None = None,
    ) -> str:
        prompt = self._build_turn_prefix_prompt(messages)
        return await self._invoke_llm(prompt, thinking_level, "Turn prefix summarization failed")

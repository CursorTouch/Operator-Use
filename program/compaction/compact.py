from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from program.llm.types import LLMContext, TextEndEvent, EndEvent, ErrorEvent, StopReason, ThinkingLevel
from program.message.types import AgentMessage, UserMessage, TextContent, CompactionSummaryMessage, BranchSummaryMessage, CustomMessage
from program.session.types import SessionEntry, CompactionEntry, MessageEntry, CustomMessageEntry, BranchEntry

from program.compaction.types import (
    CompactionSettings,
    CompactionPreparation, CompactionResult, CompactionDetails,
    FileOperations,
)
from program.compaction.utils import (
    SUMMARIZATION_SYSTEM_PROMPT, SUMMARIZATION_PROMPT, UPDATE_SUMMARIZATION_PROMPT,
    TURN_PREFIX_SUMMARIZATION_PROMPT,
    estimate_context_tokens, find_cut_point,
    get_message_from_entry, get_message_from_entry_for_compaction,
    extract_file_ops_from_message, compute_file_lists, format_file_operations,
)

if TYPE_CHECKING:
    from program.llm.service import LLM


class Compaction:
    def __init__(self, llm: LLM, settings: CompactionSettings | None = None):
        self.llm = llm
        self.settings = settings or CompactionSettings()

    def extract_file_operations(self, messages: list[AgentMessage], entries: list[SessionEntry], prev_compaction_idx:int) -> FileOperations:
        file_ops = FileOperations()

        if prev_compaction_idx >= 0:
            if isinstance(entries[prev_compaction_idx], CompactionEntry):
                prev_compaction=entries[prev_compaction_idx]
                if prev_details:=prev_compaction.details:
                    if isinstance(prev_details, CompactionDetails):
                        for f in prev_details.read_files:
                            file_ops.read.add(f)
                        for f in prev_details.modified_files:
                            file_ops.edited.add(f)

        for message in messages:
            extract_file_ops_from_message(message, file_ops)

    

    def should_compact(self, context_tokens: int, context_window: int) -> bool:
        if not self.settings.enabled:
            return False
        return context_tokens > context_window - self.settings.reserve_tokens

    def prepare(self, path_entries: list[SessionEntry]) -> CompactionPreparation | None:
        """
        Analyse path_entries (root → leaf, no header) and return everything
        needed to call compact(). Returns None if compaction is not needed or
        the session hasn't been migrated to UUID entries yet.
        """
        if path_entries and isinstance(path_entries[-1], CompactionEntry):
            return None

        prev_compaction_index = -1
        for i in reversed(range(len(path_entries))):
            if isinstance(path_entries[i], CompactionEntry):
                prev_compaction_index = i
                break

        previous_summary: str | None = None
        boundary_start = 0
        if prev_compaction_index >= 0:
            prev_compaction = path_entries[prev_compaction_index]
            assert isinstance(prev_compaction, CompactionEntry)
            previous_summary = prev_compaction.summary
            retained_idx = next(
                (i for i, e in enumerate(path_entries) if e.id == prev_compaction.first_kept_entry_id),
                -1,
            )
            boundary_start = retained_idx if retained_idx >= 0 else prev_compaction_index + 1

        boundary_end = len(path_entries)

        all_messages: list[AgentMessage] = [
            msg
            for entry in path_entries
            if (msg := get_message_from_entry(entry)) is not None
        ]
        tokens_before = estimate_context_tokens(all_messages).tokens

        cut = find_cut_point(
            path_entries, boundary_start, boundary_end, self.settings.keep_recent_tokens
        )

        if cut.first_kept_entry_index >= len(path_entries):
            return None
        first_kept_entry = path_entries[cut.first_kept_entry_index]
        if not first_kept_entry.id:
            return None

        history_end = cut.turn_start_index if cut.is_split_turn else cut.first_kept_entry_index

        messages_to_summarize: list[AgentMessage] = [
            msg
            for i in range(boundary_start, history_end)
            if (msg := get_message_from_entry_for_compaction(path_entries[i])) is not None
        ]

        turn_prefix_messages: list[AgentMessage] = []
        if cut.is_split_turn:
            turn_prefix_messages = [
                msg
                for i in range(cut.turn_start_index, cut.first_kept_entry_index)
                if (msg := get_message_from_entry_for_compaction(path_entries[i])) is not None
            ]

        file_ops = FileOperations()
        if prev_compaction_index >= 0:
            details = path_entries[prev_compaction_index].details  # type: ignore[union-attr]
            if isinstance(details, dict):
                for f in details.get("read_files", []):
                    file_ops.read.add(f)
                for f in details.get("modified_files", []):
                    file_ops.edited.add(f)

        for msg in messages_to_summarize:
            extract_file_ops_from_message(msg, file_ops)
        if cut.is_split_turn:
            for msg in turn_prefix_messages:
                extract_file_ops_from_message(msg, file_ops)

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

    async def _generate_summary(
        self,
        messages: list[AgentMessage],
        previous_summary: str | None = None,
        custom_instructions: str | None = None,
        thinking_level: ThinkingLevel | None = None,
    ) -> str:
        base_prompt = UPDATE_SUMMARIZATION_PROMPT if previous_summary else SUMMARIZATION_PROMPT
        if custom_instructions:
            base_prompt = f"{base_prompt}\n\nAdditional focus: {custom_instructions}"

        from program.compaction.utils import serialize_conversation
        conversation_text = serialize_conversation(messages)
        prompt_text = f"<conversation>\n{conversation_text}\n</conversation>\n\n"
        if previous_summary:
            prompt_text += f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
        prompt_text += base_prompt

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
            raise RuntimeError(f"Summarization failed: {error or 'Unknown error'}")

        return "".join(text_parts)

    async def _generate_turn_prefix_summary(
        self,
        messages: list[AgentMessage],
        thinking_level: ThinkingLevel | None = None,
    ) -> str:
        from program.compaction.utils import serialize_conversation
        conversation_text = serialize_conversation(messages)
        prompt_text = (
            f"<conversation>\n{conversation_text}\n</conversation>\n\n"
            f"{TURN_PREFIX_SUMMARIZATION_PROMPT}"
        )

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
            raise RuntimeError(f"Turn prefix summarization failed: {error or 'Unknown error'}")

        return "".join(text_parts)

    async def compact(
        self,
        preparation: CompactionPreparation,
        custom_instructions: str | None = None,
        thinking_level: ThinkingLevel | None = None,
    ) -> CompactionResult:
        if preparation.is_split_turn and preparation.turn_prefix_messages:
            tasks: list = [
                self._generate_turn_prefix_summary(
                    preparation.turn_prefix_messages, thinking_level
                )
            ]
            if preparation.messages_to_summarize:
                tasks.insert(
                    0,
                    self._generate_summary(
                        preparation.messages_to_summarize,
                        previous_summary=preparation.previous_summary,
                        custom_instructions=custom_instructions,
                        thinking_level=thinking_level,
                    ),
                )
            results = await asyncio.gather(*tasks)

            if preparation.messages_to_summarize:
                history_result, turn_prefix_result = results
            else:
                history_result = "No prior history."
                turn_prefix_result = results[0]

            summary = (
                f"{history_result}\n\n---\n\n"
                f"**Turn Context (split turn):**\n\n{turn_prefix_result}"
            )
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

"""Session compaction for managing large session contexts."""

from __future__ import annotations
from typing import Optional, List, TYPE_CHECKING, Any
from program.message.types import LLMMessage, Role, TextContent, ThinkingContent, ToolCallContent, ToolResultContent
from program.message.types import AssistantMessage, UserMessage, SystemMessage
from datetime import datetime

from program.session.types import (
    SessionEntry, LLMMessageEntry, CompactionSummaryEntry,
    BranchSummaryEntry, CustomMessageEntry, SessionEntryType,
)
from program.session.utils import generate_id
from program.compaction.types import (
    FileOperations, CompactionSettings, CutPointResult,
    CompactionPreparation, CompactionResult, CompactionDetails,
    ContextUsageEstimate, DEFAULT_COMPACTION_SETTINGS,
)
from program.compaction.utils import (
    create_file_ops, compute_file_lists, format_file_operations,
    extract_file_ops_from_message, should_compact, serialize_conversation,
    _get_assistant_usage, calculate_context_tokens, get_last_assistant_usage,
)

if TYPE_CHECKING:
    from program.session.manager import SessionManager
    from program.llm.service import LLM
    from program.message.types import Usage

from program.compaction.prompts import (
    SUMMARIZATION_SYSTEM_PROMPT,
    SUMMARIZATION_PROMPT,
    UPDATE_SUMMARIZATION_PROMPT,
    TURN_PREFIX_SUMMARIZATION_PROMPT,
)


# ============================================================================
# Compact Class
# ============================================================================

class Compact:
    """Session compaction service for managing large context."""

    def __init__(
        self,
        manager: SessionManager,
        settings: Optional[CompactionSettings] = None,
        llm: Optional[LLM] = None,
    ):
        self.manager = manager
        self.settings = settings or DEFAULT_COMPACTION_SETTINGS
        self.llm = llm

    # -------------------------------------------------------------------------
    # Token estimation
    # -------------------------------------------------------------------------

    def estimate_message_tokens(self, message: LLMMessage) -> int:
        """Estimate token count for a message using chars/4 heuristic."""
        chars = 0
        role = getattr(message, "role", None)
        contents = getattr(message, "contents", [])

        if role == Role.USER:
            for block in contents:
                if isinstance(block, TextContent):
                    chars += len(block.content)

        elif role == Role.ASSISTANT:
            for block in contents:
                if isinstance(block, (TextContent, ThinkingContent)):
                    chars += len(block.content)
                elif isinstance(block, ToolCallContent):
                    import json
                    chars += len(block.name) + len(json.dumps(block.args))

        elif role == Role.TOOL:
            for block in contents:
                if isinstance(block, ToolResultContent):
                    chars += len(block.content)

        return max(1, chars // 4)

    def estimate_context_tokens(self, messages: List[LLMMessage]) -> ContextUsageEstimate:
        """Estimate context size, using actual assistant usage when available."""
        last_usage_info: Optional[tuple[Usage, int]] = None
        for i in range(len(messages) - 1, -1, -1):
            usage = _get_assistant_usage(messages[i])
            if usage:
                last_usage_info = (usage, i)
                break

        if not last_usage_info:
            estimated = sum(self.estimate_message_tokens(m) for m in messages)
            return ContextUsageEstimate(tokens=estimated, usage_tokens=0, trailing_tokens=estimated)

        usage, idx = last_usage_info
        usage_tokens = calculate_context_tokens(usage)
        trailing = sum(self.estimate_message_tokens(messages[i]) for i in range(idx + 1, len(messages)))
        return ContextUsageEstimate(
            tokens=usage_tokens + trailing,
            usage_tokens=usage_tokens,
            trailing_tokens=trailing,
            last_usage_index=idx,
        )

    def should_perform_compaction(self, entries: Optional[List[SessionEntry]] = None) -> bool:
        """Check if compaction should be performed on current session."""
        if entries is None:
            entries = self.manager.get_entries()

        context = self.manager.build_session_context()
        estimate = self.estimate_context_tokens(context.messages)

        context_window = self.llm.model.context_window if self.llm else 200_000
        return should_compact(estimate.tokens, context_window, self.settings)

    # -------------------------------------------------------------------------
    # Cut point detection
    # -------------------------------------------------------------------------

    def _find_valid_cut_points(
        self,
        entries: List[SessionEntry],
        start_index: int,
        end_index: int,
    ) -> List[int]:
        """Find valid cut points — never cut at tool results."""
        cut_points: List[int] = []
        for i in range(start_index, end_index):
            entry = entries[i]
            if entry.type == SessionEntryType.LLM and isinstance(entry, LLMMessageEntry):
                if entry.message.role != Role.TOOL:
                    cut_points.append(i)
            elif entry.type in (SessionEntryType.BRANCH_SUMMARY, SessionEntryType.CUSTOM):
                cut_points.append(i)
        return cut_points

    def _find_turn_start_index(
        self,
        entries: List[SessionEntry],
        entry_index: int,
        start_index: int,
    ) -> int:
        """Find the user message that starts the turn containing the given entry."""
        for i in range(entry_index, start_index - 1, -1):
            entry = entries[i]
            if entry.type in (SessionEntryType.BRANCH_SUMMARY, SessionEntryType.CUSTOM):
                return i
            if entry.type == SessionEntryType.LLM and isinstance(entry, LLMMessageEntry):
                if entry.message.role == Role.USER:
                    return i
        return -1

    def find_cut_point(
        self,
        entries: List[SessionEntry],
        start_index: int,
        end_index: int,
        keep_recent_tokens: Optional[int] = None,
    ) -> CutPointResult:
        """Find the cut point that keeps approximately `keep_recent_tokens`."""
        if keep_recent_tokens is None:
            keep_recent_tokens = self.settings.keep_recent_tokens

        cut_points = self._find_valid_cut_points(entries, start_index, end_index)
        if not cut_points:
            return CutPointResult(first_kept_entry_index=start_index)

        accumulated = 0
        cut_index = cut_points[0]

        for i in range(end_index - 1, start_index - 1, -1):
            entry = entries[i]
            if entry.type == SessionEntryType.LLM and isinstance(entry, LLMMessageEntry):
                accumulated += self.estimate_message_tokens(entry.message)
            elif isinstance(entry, BranchSummaryEntry):
                accumulated += max(1, len(entry.summary) // 4)
            elif isinstance(entry, CustomMessageEntry) and isinstance(entry.content, str):
                accumulated += max(1, len(entry.content) // 4)
            else:
                continue
            if accumulated >= keep_recent_tokens:
                for c in cut_points:
                    if c >= i:
                        cut_index = c
                        break
                break

        # Pull cut_index back to include preceding non-message entries
        while cut_index > start_index:
            prev = entries[cut_index - 1]
            if prev.type == SessionEntryType.COMPACTION_SUMMARY:
                break
            if prev.type == SessionEntryType.LLM:
                break
            cut_index -= 1

        cut_entry = entries[cut_index]
        is_user = (
            cut_entry.type == SessionEntryType.LLM
            and isinstance(cut_entry, LLMMessageEntry)
            and cut_entry.message.role == Role.USER
        )
        turn_start = -1 if is_user else self._find_turn_start_index(entries, cut_index, start_index)

        return CutPointResult(
            first_kept_entry_index=cut_index,
            turn_start_index=turn_start,
            is_split_turn=not is_user and turn_start != -1,
        )

    # -------------------------------------------------------------------------
    # Message extraction helpers
    # -------------------------------------------------------------------------

    def _entry_to_message(self, entry: SessionEntry, skip_compaction: bool = False) -> Optional[LLMMessage]:
        """Convert a session entry to an LLM message for summarization."""
        if skip_compaction and entry.type == SessionEntryType.COMPACTION_SUMMARY:
            return None
        if isinstance(entry, LLMMessageEntry):
            return entry.message
        if isinstance(entry, CustomMessageEntry):
            msg = UserMessage()
            content = entry.content
            msg.contents = [TextContent(content=content if isinstance(content, str) else "")]
            return msg
        if isinstance(entry, BranchSummaryEntry) and entry.summary:
            msg = UserMessage()
            msg.contents = [TextContent(content=entry.summary)]
            return msg
        if isinstance(entry, CompactionSummaryEntry) and not skip_compaction and entry.summary:
            msg = UserMessage()
            msg.contents = [TextContent(content=entry.summary)]
            return msg
        return None

    # -------------------------------------------------------------------------
    # File operation extraction
    # -------------------------------------------------------------------------

    def _extract_file_operations(
        self,
        messages: List[LLMMessage],
        entries: List[SessionEntry],
        prev_compaction_index: int,
    ) -> FileOperations:
        """Build FileOperations, seeding from previous compaction details."""
        file_ops = create_file_ops()

        if prev_compaction_index >= 0:
            prev = entries[prev_compaction_index]
            if isinstance(prev, CompactionSummaryEntry) and not getattr(prev, "from_hook", False):
                details = prev.details
                if isinstance(details, dict):
                    for f in details.get("read_files", []):
                        file_ops.read.add(f)
                    for f in details.get("modified_files", []):
                        file_ops.edited.add(f)

        for msg in messages:
            extract_file_ops_from_message(msg, file_ops)

        return file_ops

    # -------------------------------------------------------------------------
    # Preparation
    # -------------------------------------------------------------------------

    def prepare(self, entries: Optional[List[SessionEntry]] = None) -> Optional[CompactionPreparation]:
        """Prepare compaction data from session entries."""
        if entries is None:
            entries = self.manager.get_entries()

        if not entries or entries[-1].type == SessionEntryType.COMPACTION_SUMMARY:
            return None

        prev_compaction_index = -1
        for i in range(len(entries) - 1, -1, -1):
            if entries[i].type == SessionEntryType.COMPACTION_SUMMARY:
                prev_compaction_index = i
                break

        previous_summary: Optional[str] = None
        boundary_start = 0
        if prev_compaction_index >= 0:
            prev = entries[prev_compaction_index]
            if isinstance(prev, CompactionSummaryEntry):
                previous_summary = prev.summary
                first_kept_id = prev.first_kept_entry_id
                idx = next((i for i, e in enumerate(entries) if e.id == first_kept_id), prev_compaction_index + 1)
                boundary_start = idx

        context = self.manager.build_session_context()
        tokens_before = self.estimate_context_tokens(context.messages).tokens

        cut_point = self.find_cut_point(entries, boundary_start, len(entries))

        first_kept_entry = entries[cut_point.first_kept_entry_index]
        if not first_kept_entry.id:
            return None

        history_end = cut_point.turn_start_index if cut_point.is_split_turn else cut_point.first_kept_entry_index

        messages_to_summarize: List[LLMMessage] = []
        for e in entries[boundary_start:history_end]:
            msg = self._entry_to_message(e, skip_compaction=True)
            if msg:
                messages_to_summarize.append(msg)

        turn_prefix_messages: List[LLMMessage] = []
        if cut_point.is_split_turn:
            for e in entries[cut_point.turn_start_index:cut_point.first_kept_entry_index]:
                msg = self._entry_to_message(e, skip_compaction=True)
                if msg:
                    turn_prefix_messages.append(msg)

        file_ops = self._extract_file_operations(messages_to_summarize, entries, prev_compaction_index)
        if cut_point.is_split_turn:
            for msg in turn_prefix_messages:
                extract_file_ops_from_message(msg, file_ops)

        return CompactionPreparation(
            first_kept_entry_id=first_kept_entry.id,
            messages_to_summarize=messages_to_summarize,
            turn_prefix_messages=turn_prefix_messages,
            is_split_turn=cut_point.is_split_turn,
            tokens_before=tokens_before,
            previous_summary=previous_summary,
            file_ops=file_ops,
            settings=self.settings,
        )

    # -------------------------------------------------------------------------
    # LLM summarization
    # -------------------------------------------------------------------------

    async def generate_summary(
        self,
        messages: List[LLMMessage],
        llm: LLM,
        custom_instructions: Optional[str] = None,
        previous_summary: Optional[str] = None,
    ) -> str:
        """Generate a summary of the conversation using the LLM."""
        from program.llm.types import TextEndEvent, ErrorEvent, StopReason

        base_prompt = UPDATE_SUMMARIZATION_PROMPT if previous_summary else SUMMARIZATION_PROMPT
        if custom_instructions:
            base_prompt = f"{base_prompt}\n\nAdditional focus: {custom_instructions}"

        conversation_text = serialize_conversation(messages)
        prompt_parts = [f"<conversation>\n{conversation_text}\n</conversation>"]
        if previous_summary:
            prompt_parts.append(f"<previous-summary>\n{previous_summary}\n</previous-summary>")
        prompt_parts.append(base_prompt)
        prompt_text = "\n\n".join(prompt_parts)

        system_msg = SystemMessage(contents=[TextContent(content=SUMMARIZATION_SYSTEM_PROMPT)])
        user_msg = UserMessage(contents=[TextContent(content=prompt_text)])

        messages=[system_msg, user_msg]

        events = await llm.invoke(messages)

        text_parts: List[str] = []
        error = ""
        stop_reason = StopReason.Stop
        for event in events:
            if isinstance(event, TextEndEvent):
                text_parts.append(event.text.content)
            elif isinstance(event, ErrorEvent):
                stop_reason = event.reason
                error = event.error

        if stop_reason == StopReason.Error:
            raise RuntimeError(f"Summarization failed: {error}")

        return "".join(text_parts)

    async def _generate_turn_prefix_summary(
        self,
        messages: List[LLMMessage],
        llm: LLM,
    ) -> str:
        """Generate a summary for the prefix of a split turn."""
        from program.llm.types import TextEndEvent, ErrorEvent, StopReason

        conversation_text = serialize_conversation(messages)
        prompt_text = f"<conversation>\n{conversation_text}\n</conversation>\n\n{TURN_PREFIX_SUMMARIZATION_PROMPT}"

        system_msg = SystemMessage(contents=[TextContent(content=SUMMARIZATION_SYSTEM_PROMPT)])
        user_msg = UserMessage(contents=[TextContent(content=prompt_text)])

        messages=[system_msg, user_msg]

        events = await llm.invoke(messages)

        text_parts: List[str] = []
        error = ""
        stop_reason = StopReason.Stop
        for event in events:
            if isinstance(event, TextEndEvent):
                text_parts.append(event.text.content)
            elif isinstance(event, ErrorEvent):
                stop_reason = event.reason
                error = event.error

        if stop_reason == StopReason.Error:
            raise RuntimeError(f"Turn prefix summarization failed: {error}")

        return "".join(text_parts)

    def _generate_basic_summary(self, preparation: CompactionPreparation) -> str:
        """Fallback summary when no LLM is available."""
        sections = []
        if preparation.previous_summary:
            sections.append(f"## Previous Summary\n{preparation.previous_summary}")
        sections.append(f"## Compacted Messages\n- Summarized {len(preparation.messages_to_summarize)} messages")
        if preparation.is_split_turn:
            sections.append(f"## Split Turn Context\n- Prefix messages: {len(preparation.turn_prefix_messages)}")
        return "\n\n".join(sections)

    # -------------------------------------------------------------------------
    # Execution
    # -------------------------------------------------------------------------

    async def execute(
        self,
        preparation: Optional[CompactionPreparation] = None,
        llm: Optional[LLM] = None,
        custom_instructions: Optional[str] = None,
    ) -> Optional[str]:
        """Perform session compaction, returning the new entry id or None."""
        if preparation is None:
            preparation = self.prepare()
            if preparation is None:
                return None

        llm = llm or self.llm

        if llm and preparation.is_split_turn and preparation.turn_prefix_messages:
            import asyncio
            if preparation.messages_to_summarize:
                history_summary, prefix_summary = await asyncio.gather(
                    self.generate_summary(
                        preparation.messages_to_summarize, llm,
                        custom_instructions, preparation.previous_summary,
                    ),
                    self._generate_turn_prefix_summary(
                        preparation.turn_prefix_messages, llm
                    ),
                )
            else:
                history_summary = "No prior history."
                prefix_summary = await self._generate_turn_prefix_summary(
                    preparation.turn_prefix_messages, llm
                )
            summary = f"{history_summary}\n\n---\n\n**Turn Context (split turn):**\n\n{prefix_summary}"
        elif llm:
            summary = await self.generate_summary(
                preparation.messages_to_summarize, llm,
                custom_instructions, preparation.previous_summary,
            )
        else:
            summary = self._generate_basic_summary(preparation)

        files_dict = compute_file_lists(preparation.file_ops or create_file_ops())
        summary += format_file_operations(files_dict["read_files"], files_dict["modified_files"])

        entry = CompactionSummaryEntry(
            id=generate_id(set(self.manager.by_id.keys())),
            parent_id=self.manager.leaf_id,
            timestamp=datetime.now().isoformat(),
            summary=summary,
            first_kept_entry_id=preparation.first_kept_entry_id,
            tokens_before=preparation.tokens_before,
            details={
                "read_files": files_dict["read_files"],
                "modified_files": files_dict["modified_files"],
            },
        )

        self.manager._append_entry(entry)
        return entry.id

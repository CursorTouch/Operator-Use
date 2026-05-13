"""Session compaction service for managing large session contexts."""

from __future__ import annotations
from typing import Optional, List, Dict, Any, Set, TYPE_CHECKING
from datetime import datetime

from program.session.types import (
    SessionEntry,
    LLMMessageEntry,
    CompactionEntry,
    BranchSummaryEntry,
    CustomMessageEntry,
    SessionEntryType,
)
from program.session.utils import generate_id
from program.compaction.types import (
    FileOperations,
    CompactionSettings,
    CutPointResult,
    CompactionPreparation,
    CompactionResult,
    CompactionDetails,
    DEFAULT_COMPACTION_SETTINGS,
)
from program.compaction.utils import (
    create_file_ops,
    compute_file_lists,
    format_file_operations,
    truncate_for_summary,
)

if TYPE_CHECKING:
    from program.session.manager import SessionManager


# Summarization prompts
SUMMARIZATION_SYSTEM_PROMPT = """You are a context summarization assistant. Your task is to read a conversation between a user and an AI coding assistant, then produce a structured summary following the exact format specified.

Do NOT continue the conversation. Do NOT respond to any questions in the conversation. ONLY output the structured summary."""

SUMMARIZATION_PROMPT = """The messages above are a conversation to summarize. Create a structured context checkpoint summary that another LLM will use to continue the work.

Use this EXACT format:

## Goal
[What is the user trying to accomplish? Can be multiple items if the session covers different tasks.]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned by user]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Current work]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [Any data, examples, or references needed to continue]
- [Or "(none)" if not applicable]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

UPDATE_SUMMARIZATION_PROMPT = """The messages above are NEW conversation messages to incorporate into the existing summary provided in <previous-summary> tags.

Update the existing structured summary with new information. RULES:
- PRESERVE all existing information from the previous summary
- ADD new progress, decisions, and context from the new messages
- UPDATE the Progress section: move items from "In Progress" to "Done" when completed
- UPDATE "Next Steps" based on what was accomplished
- PRESERVE exact file paths, function names, and error messages
- If something is no longer relevant, you may remove it

Use this EXACT format:

## Goal
[Preserve existing goals, add new ones if the task expanded]

## Constraints & Preferences
- [Preserve existing, add new ones discovered]

## Progress
### Done
- [x] [Include previously done items AND newly completed items]

### In Progress
- [ ] [Current work - update based on progress]

### Blocked
- [Current blockers - remove if resolved]

## Key Decisions
- **[Decision]**: [Brief rationale] (preserve all previous, add new)

## Next Steps
1. [Update based on current state]

## Critical Context
- [Preserve important context, add new if needed]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

TURN_PREFIX_SUMMARIZATION_PROMPT = """This is the PREFIX of a turn that was too large to keep. The SUFFIX (recent work) is retained.

Summarize the prefix to provide context for the retained suffix:

## Original Request
[What did the user ask for in this turn?]

## Early Progress
- [Key decisions and work done in the prefix]

## Context for Suffix
- [Information needed to understand the retained recent work]

Be concise. Focus on what's needed to understand the kept suffix."""


# ============================================================================
# Compact Service Class
# ============================================================================


class Compact:
    """Session compaction service for managing large context."""

    def __init__(
        self,
        manager: SessionManager,
        settings: Optional[CompactionSettings] = None,
    ):
        """Initialize compaction service.

        Args:
            manager: SessionManager instance
            settings: Compaction settings (uses defaults if None)
        """
        self.manager = manager
        self.settings = settings or DEFAULT_COMPACTION_SETTINGS

    def estimate_message_tokens(self, message: Any) -> int:
        """Estimate token count for a message using chars/4 heuristic.

        Args:
            message: Message to estimate tokens for

        Returns:
            Estimated token count
        """
        char_count = 0

        if hasattr(message, "role"):
            if message.role == "user":
                if hasattr(message, "content"):
                    if isinstance(message.content, str):
                        char_count = len(message.content)
                    elif isinstance(message.content, list):
                        for block in message.content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                char_count += len(block.get("text", ""))

            elif message.role == "assistant":
                if hasattr(message, "contents"):
                    for content_block in message.contents:
                        if hasattr(content_block, "type"):
                            if content_block.type == "text" and hasattr(content_block, "content"):
                                char_count += len(content_block.content)
                            elif content_block.type == "thinking" and hasattr(content_block, "content"):
                                char_count += len(content_block.content)

        return max(1, char_count // 4)

    def estimate_context_tokens(self, messages: List[Any]) -> int:
        """Estimate total context tokens from messages.

        Args:
            messages: List of messages

        Returns:
            Estimated total tokens
        """
        total = 0
        for message in messages:
            total += self.estimate_message_tokens(message)
        return total

    def _find_valid_cut_points(
        self,
        entries: List[SessionEntry],
        start_index: int,
        end_index: int,
    ) -> List[int]:
        """Find valid cut points in entries."""
        cut_points: List[int] = []

        for i in range(start_index, end_index):
            entry = entries[i]

            if entry.type in (
                SessionEntryType.THINKING_LEVEL_CHANGE,
                SessionEntryType.MODEL_CHANGE,
                SessionEntryType.LABEL,
                SessionEntryType.SESSION_INFO,
                SessionEntryType.CUSTOM_INFO,
                SessionEntryType.COMPACTION,
            ):
                continue

            if entry.type in (
                SessionEntryType.LLM,
                SessionEntryType.CUSTOM,
                SessionEntryType.BRANCH_SUMMARY,
            ):
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
                if hasattr(entry.message, "role"):
                    from program.message.types import Role

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
        """Find the cut point for compaction.

        Args:
            entries: Session entries to analyze
            start_index: Start of search range (inclusive)
            end_index: End of search range (exclusive)
            keep_recent_tokens: Target tokens to keep (uses settings if None)

        Returns:
            CutPointResult with cut point details
        """
        if keep_recent_tokens is None:
            keep_recent_tokens = self.settings.keep_recent_tokens

        cut_points = self._find_valid_cut_points(entries, start_index, end_index)

        if not cut_points:
            return CutPointResult(
                first_kept_entry_index=start_index,
                turn_start_index=-1,
                is_split_turn=False,
            )

        accumulated_tokens = 0
        cut_index = cut_points[0]

        for i in range(end_index - 1, start_index - 1, -1):
            entry = entries[i]

            if entry.type != SessionEntryType.LLM:
                continue

            if isinstance(entry, LLMMessageEntry):
                message_tokens = self.estimate_message_tokens(entry.message)
                accumulated_tokens += message_tokens

                if accumulated_tokens >= keep_recent_tokens:
                    for c in cut_points:
                        if c >= i:
                            cut_index = c
                            break
                    break

        while cut_index > start_index:
            prev_entry = entries[cut_index - 1]
            if prev_entry.type == SessionEntryType.COMPACTION:
                break
            if prev_entry.type == SessionEntryType.LLM:
                break
            cut_index -= 1

        cut_entry = entries[cut_index]
        is_user_message = (
            cut_entry.type == SessionEntryType.LLM
            and isinstance(cut_entry, LLMMessageEntry)
            and hasattr(cut_entry.message, "role")
        )

        if is_user_message:
            from program.message.types import Role

            is_user_message = cut_entry.message.role == Role.USER

        turn_start_index = (
            -1
            if is_user_message
            else self._find_turn_start_index(entries, cut_index, start_index)
        )

        return CutPointResult(
            first_kept_entry_index=cut_index,
            turn_start_index=turn_start_index,
            is_split_turn=not is_user_message and turn_start_index != -1,
        )

    def prepare(self, entries: Optional[List[SessionEntry]] = None) -> Optional[CompactionPreparation]:
        """Prepare compaction data from session entries.

        Args:
            entries: Session entries (uses manager entries if None)

        Returns:
            CompactionPreparation with message lists and metadata, or None if not needed
        """
        if entries is None:
            entries = self.manager.get_entries()

        if not entries or entries[-1].type == SessionEntryType.COMPACTION:
            return None

        prev_compaction_index = -1
        for i in range(len(entries) - 1, -1, -1):
            if entries[i].type == SessionEntryType.COMPACTION:
                prev_compaction_index = i
                break

        previous_summary: Optional[str] = None
        boundary_start = 0

        if prev_compaction_index >= 0:
            prev_compaction = entries[prev_compaction_index]
            if isinstance(prev_compaction, CompactionEntry):
                previous_summary = prev_compaction.summary
                first_kept_id = prev_compaction.first_kept_entry_id
                first_kept_index = next(
                    (i for i, e in enumerate(entries) if e.id == first_kept_id),
                    prev_compaction_index + 1,
                )
                boundary_start = first_kept_index

        boundary_end = len(entries)

        tokens_before = self.estimate_context_tokens(
            [e.message if isinstance(e, LLMMessageEntry) else None for e in entries]
        )

        cut_point = self.find_cut_point(entries, boundary_start, boundary_end)

        first_kept_entry = entries[cut_point.first_kept_entry_index]
        first_kept_entry_id = first_kept_entry.id

        history_end = (
            cut_point.turn_start_index
            if cut_point.is_split_turn
            else cut_point.first_kept_entry_index
        )

        messages_to_summarize = [
            e.message
            for e in entries[boundary_start:history_end]
            if isinstance(e, LLMMessageEntry)
        ]

        turn_prefix_messages = []
        if cut_point.is_split_turn:
            turn_prefix_messages = [
                e.message
                for e in entries[cut_point.turn_start_index : cut_point.first_kept_entry_index]
                if isinstance(e, LLMMessageEntry)
            ]

        file_ops = create_file_ops()

        return CompactionPreparation(
            first_kept_entry_id=first_kept_entry_id,
            messages_to_summarize=messages_to_summarize,
            turn_prefix_messages=turn_prefix_messages,
            is_split_turn=cut_point.is_split_turn,
            tokens_before=tokens_before,
            previous_summary=previous_summary,
            file_ops=file_ops,
            settings=self.settings,
        )

    async def execute(self, preparation: Optional[CompactionPreparation] = None) -> Optional[str]:
        """Perform session compaction.

        Args:
            preparation: Pre-calculated preparation (None to auto-prepare)

        Returns:
            ID of the created compaction entry, or None if compaction not needed
        """
        if preparation is None:
            preparation = self.prepare()
            if preparation is None:
                return None

        summary = self._generate_basic_summary(preparation)

        if preparation.file_ops:
            files_dict = compute_file_lists(preparation.file_ops)
            file_ops_str = format_file_operations(
                files_dict["read_files"],
                files_dict["modified_files"],
            )
            summary += file_ops_str

        entry_id = generate_id(set(self.manager.by_id.keys()))
        timestamp = datetime.now().isoformat()

        compaction_entry = CompactionEntry(
            id=entry_id,
            parent_id=self.manager.leaf_id,
            timestamp=timestamp,
            summary=summary,
            first_kept_entry_id=preparation.first_kept_entry_id,
            tokens_before=preparation.tokens_before,
            details={
                "read_files": [],
                "modified_files": [],
            },
        )

        self.manager._append_entry(compaction_entry)
        return entry_id

    def _generate_basic_summary(self, preparation: CompactionPreparation) -> str:
        """Generate a basic summary from preparation data."""
        sections = []

        if preparation.previous_summary:
            sections.append(f"## Previous Summary\n{preparation.previous_summary}")

        message_count = len(preparation.messages_to_summarize)
        sections.append(f"## Compacted Messages\n- Summarized {message_count} messages")

        if preparation.is_split_turn:
            prefix_count = len(preparation.turn_prefix_messages)
            sections.append(f"## Split Turn Context\n- Prefix messages: {prefix_count}")

        return "\n\n".join(sections)

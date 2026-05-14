"""Branch summarization for session tree navigation.

When navigating to a different point in the session tree, this generates
a summary of the branch being left so context isn't lost.
"""

from __future__ import annotations
from typing import Optional, List, Dict, Any, Set, TYPE_CHECKING
from datetime import datetime

from program.session.types import (
    SessionEntry,
    LLMMessageEntry,
    BranchSummaryEntry,
    CompactionSummaryEntry,
    CustomMessageEntry,
    SessionEntryType,
)
from program.session.utils import generate_id
from program.compaction.types import (
    FileOperations,
    CompactionSettings,
    BranchSummaryResult,
    BranchSummaryDetails,
    BranchPreparation,
    CollectEntriesResult,
)
from program.compaction.utils import (
    create_file_ops,
    compute_file_lists,
    format_file_operations,
    truncate_for_summary,
)
from program.compaction.compact import Compact

if TYPE_CHECKING:
    from program.session.manager import SessionManager


# ============================================================================
# Constants
# ============================================================================

BRANCH_SUMMARY_PREAMBLE = """The user explored a different conversation branch before returning here.
Summary of that exploration:

"""

BRANCH_SUMMARY_PROMPT = """Create a structured summary of this conversation branch for context when returning later.

Use this EXACT format:

## Goal
[What was the user trying to accomplish in this branch?]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Work that was started but not finished]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [What should happen next to continue this work]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

SUMMARIZATION_SYSTEM_PROMPT = """You are a context summarization assistant. Your task is to read a conversation between a user and an AI coding assistant, then produce a structured summary following the exact format specified.

Do NOT continue the conversation. Do NOT respond to any questions in the conversation. ONLY output the structured summary."""


# ============================================================================
# BranchCompact Service Class
# ============================================================================


class BranchCompact:
    """Branch summarization service for session tree navigation."""

    def __init__(self, manager: SessionManager):
        """Initialize branch compaction service.

        Args:
            manager: SessionManager instance
        """
        self.manager = manager
        self.compact = Compact(manager)

    def collect_entries(
        self,
        old_leaf_id: Optional[str],
        target_id: str,
    ) -> CollectEntriesResult:
        """Collect entries that should be summarized when navigating branches.

        Walks from old_leaf_id back to the common ancestor with target_id,
        collecting entries along the way.

        Args:
            old_leaf_id: Current position (where we're navigating from)
            target_id: Target position (where we're navigating to)

        Returns:
            CollectEntriesResult with entries to summarize and common ancestor ID
        """
        # If no old position, nothing to summarize
        if not old_leaf_id:
            return CollectEntriesResult()

        # Find common ancestor (deepest node that's on both paths)
        old_path = {e.id for e in self.manager.get_branch(old_leaf_id)}
        target_path = self.manager.get_branch(target_id)

        # target_path is root-first, iterate backwards to find deepest common ancestor
        common_ancestor_id: Optional[str] = None
        for i in range(len(target_path) - 1, -1, -1):
            if target_path[i].id in old_path:
                common_ancestor_id = target_path[i].id
                break

        # Collect entries from old leaf back to common ancestor
        entries: List[SessionEntry] = []
        current_id: Optional[str] = old_leaf_id

        while current_id and current_id != common_ancestor_id:
            entry = self.manager.get_entry(current_id)
            if not entry:
                break
            entries.append(entry)
            current_id = entry.parent_id

        # Reverse to get chronological order
        entries.reverse()

        return CollectEntriesResult(entries=entries, common_ancestor_id=common_ancestor_id)

    def _get_message_from_entry(self, entry: SessionEntry) -> Optional[Any]:
        """Extract message from a session entry."""
        if entry.type == SessionEntryType.LLM and isinstance(entry, LLMMessageEntry):
            # Skip tool results - context is in assistant's tool call
            if hasattr(entry.message, "role"):
                from program.message.types import Role

                if entry.message.role == Role.TOOL_RESULT:
                    return None
            return entry.message

        if entry.type == SessionEntryType.CUSTOM and isinstance(entry, CustomMessageEntry):
            return entry

        if entry.type == SessionEntryType.BRANCH_SUMMARY and isinstance(entry, BranchSummaryEntry):
            return entry

        if entry.type == SessionEntryType.COMPACTION_SUMMARY and isinstance(entry, CompactionSummaryEntry):
            return entry

        return None

    def prepare(self, entries: List[SessionEntry], token_budget: int = 0) -> BranchPreparation:
        """Prepare entries for summarization with token budget.

        Walks entries from NEWEST to OLDEST, adding messages until token budget.
        This keeps most recent context when branch is too long.

        Args:
            entries: Entries in chronological order
            token_budget: Maximum tokens to include (0 = no limit)

        Returns:
            BranchPreparation with messages, file ops, and token count
        """
        messages: List[Any] = []
        file_ops = create_file_ops()
        total_tokens = 0

        # First pass: collect file ops from ALL entries
        for entry in entries:
            if entry.type == SessionEntryType.BRANCH_SUMMARY and isinstance(entry, BranchSummaryEntry):
                # Only extract from pi-generated summaries
                if not getattr(entry, "from_hook", False) and entry.details:
                    try:
                        if isinstance(entry.details, dict):
                            details = entry.details
                        elif isinstance(entry.details, BranchSummaryDetails):
                            details = {"read_files": entry.details.read_files, "modified_files": entry.details.modified_files}
                        else:
                            details = {}

                        if "read_files" in details and isinstance(details["read_files"], list):
                            for f in details["read_files"]:
                                file_ops.read.add(f)

                        if "modified_files" in details and isinstance(details["modified_files"], list):
                            for f in details["modified_files"]:
                                file_ops.edited.add(f)
                    except (AttributeError, TypeError):
                        pass

        # Second pass: walk from newest to oldest, adding messages until token budget
        for i in range(len(entries) - 1, -1, -1):
            entry = entries[i]
            message = self._get_message_from_entry(entry)
            if not message:
                continue

            tokens = self.compact.estimate_message_tokens(message) if hasattr(message, "role") else 0

            # Check budget before adding
            if token_budget > 0 and total_tokens + tokens > token_budget:
                # If this is a summary entry, try to fit it anyway as it's important context
                if entry.type in (SessionEntryType.COMPACTION, SessionEntryType.BRANCH_SUMMARY):
                    if total_tokens < token_budget * 0.9:
                        messages.insert(0, message)
                        total_tokens += tokens
                # Stop - we've hit the budget
                break

            messages.insert(0, message)
            total_tokens += tokens

        return BranchPreparation(messages=messages, file_ops=file_ops, total_tokens=total_tokens)

    async def generate_summary(
        self,
        entries: List[SessionEntry],
        custom_instructions: Optional[str] = None,
        reserve_tokens: int = 16384,
    ) -> BranchSummaryResult:
        """Generate a summary of abandoned branch entries.

        Args:
            entries: Session entries to summarize (chronological order)
            custom_instructions: Optional custom instructions for summarization
            reserve_tokens: Tokens reserved for prompt + response

        Returns:
            BranchSummaryResult with summary and file tracking
        """
        if not entries:
            return BranchSummaryResult(summary="No content to summarize")

        # Prepare entries
        preparation = self.prepare(entries)

        if not preparation.messages:
            return BranchSummaryResult(summary="No content to summarize")

        # Generate basic summary
        summary = self._generate_basic_summary(preparation, custom_instructions)

        # Compute file lists
        files_dict = compute_file_lists(preparation.file_ops)
        file_ops_str = format_file_operations(
            files_dict["read_files"],
            files_dict["modified_files"],
        )
        summary += file_ops_str

        return BranchSummaryResult(
            summary=summary,
            read_files=files_dict["read_files"],
            modified_files=files_dict["modified_files"],
        )

    def _generate_basic_summary(
        self,
        preparation: BranchPreparation,
        custom_instructions: Optional[str] = None,
    ) -> str:
        """Generate a basic branch summary."""
        sections = []

        sections.append(BRANCH_SUMMARY_PREAMBLE)

        message_count = len(preparation.messages)
        sections.append(f"## Branch Content\n- Summarized {message_count} messages")
        sections.append(f"- Total tokens: {preparation.total_tokens}")

        if custom_instructions:
            sections.append(f"## Custom Focus\n{custom_instructions}")

        return "\n\n".join(sections)

    def create_entry(
        self,
        summary: str,
        read_files: Optional[List[str]] = None,
        modified_files: Optional[List[str]] = None,
    ) -> str:
        """Create and append a branch summary entry to the session.

        Args:
            summary: Summary text
            read_files: Files that were read in the branch
            modified_files: Files that were modified in the branch

        Returns:
            ID of the created branch summary entry
        """
        entry_id = generate_id(set(self.manager.by_id.keys()))
        timestamp = datetime.now().isoformat()

        details = BranchSummaryDetails(
            read_files=read_files or [],
            modified_files=modified_files or [],
        )

        entry = BranchSummaryEntry(
            id=entry_id,
            parent_id=self.manager.leaf_id,
            timestamp=timestamp,
            from_id=self.manager.leaf_id or "root",
            summary=summary,
            details=details,
        )

        self.manager._append_entry(entry)
        return entry_id

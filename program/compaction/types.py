"""Compaction module types and data structures."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List, Set, Any, Dict
from program.message.types import LLMMessage
from enum import Enum


class CompactionMode(str, Enum):
    """Modes for compaction operations."""
    FULL = "full"
    SPLIT_TURN = "split_turn"


@dataclass
class FileOperations:
    """Tracks file read and write operations during a session."""
    read: Set[str] = field(default_factory=set)
    written: Set[str] = field(default_factory=set)
    edited: Set[str] = field(default_factory=set)


@dataclass
class ContextUsageEstimate:
    """Estimated token usage for context."""
    tokens: int = 0
    usage_tokens: int = 0
    trailing_tokens: int = 0
    last_usage_index: Optional[int] = None


@dataclass
class CompactionSettings:
    """Settings for session compaction."""
    enabled: bool = True
    reserve_tokens: int = 16384
    keep_recent_tokens: int = 20000


@dataclass
class CutPointResult:
    """Result of finding a cut point for compaction."""
    first_kept_entry_index: int
    turn_start_index: int = -1
    is_split_turn: bool = False


@dataclass
class CompactionPreparation:
    """Prepared data for compaction before summarization."""
    first_kept_entry_id: str
    messages_to_summarize: List[LLMMessage]
    turn_prefix_messages: List[LLMMessage]
    is_split_turn: bool
    tokens_before: int
    previous_summary: Optional[str] = None
    file_ops: Optional[FileOperations] = None
    settings: Optional[CompactionSettings] = None


@dataclass
class CompactionResult:
    """Result from compaction - with summary and metadata."""
    summary: str
    first_kept_entry_id: str
    tokens_before: int
    details: Optional[Dict[str, Any]] = None


@dataclass
class CompactionDetails:
    """Details stored in CompactionEntry for file tracking."""
    read_files: List[str] = field(default_factory=list)
    modified_files: List[str] = field(default_factory=list)


@dataclass
class BranchSummaryResult:
    """Result from branch summarization."""
    summary: Optional[str] = None
    read_files: List[str] = field(default_factory=list)
    modified_files: List[str] = field(default_factory=list)
    aborted: bool = False
    error: Optional[str] = None


@dataclass
class BranchSummaryDetails:
    """Details stored in BranchSummaryEntry for file tracking."""
    read_files: List[str] = field(default_factory=list)
    modified_files: List[str] = field(default_factory=list)


@dataclass
class BranchPreparation:
    """Prepared data for branch summarization."""
    messages: List[Any] = field(default_factory=list)
    file_ops: Optional[FileOperations] = None
    total_tokens: int = 0


@dataclass
class CollectEntriesResult:
    """Result from collecting entries for branch summary."""
    entries: List[Any] = field(default_factory=list)
    common_ancestor_id: Optional[str] = None


DEFAULT_COMPACTION_SETTINGS = CompactionSettings()

"""Session compaction module for managing large conversation contexts.

This module provides:
- Compact class: Session compaction and context management
- BranchCompact class: Branch summarization for tree navigation
- Shared types and utilities for both

It integrates with the session manager to handle context overflow gracefully.
"""

from program.compaction.types import (
    CompactionSettings,
    CompactionPreparation,
    CompactionResult,
    CompactionDetails,
    FileOperations,
    BranchSummaryResult,
    BranchSummaryDetails,
    BranchPreparation,
    CollectEntriesResult,
    DEFAULT_COMPACTION_SETTINGS,
)
from program.compaction.compact import Compact
from program.compaction.branch_compact import BranchCompact
from program.compaction.utils import (
    compute_file_lists,
    format_file_operations,
    create_file_ops,
)

__all__ = [
    # Types
    "CompactionSettings",
    "CompactionPreparation",
    "CompactionResult",
    "CompactionDetails",
    "FileOperations",
    "DEFAULT_COMPACTION_SETTINGS",
    # Compaction service class
    "Compact",
    # Branch summarization class
    "BranchCompact",
    "BranchSummaryResult",
    "BranchSummaryDetails",
    "BranchPreparation",
    "CollectEntriesResult",
    # Utils
    "compute_file_lists",
    "format_file_operations",
    "create_file_ops",
]

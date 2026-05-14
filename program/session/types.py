from __future__ import annotations
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from program.message.types import LLMMessage
from program.llm.types import ThinkingLevel
from pathlib import Path

class SessionEntryType(str, Enum):
    SESSION_HEADER = "session_header"
    LLM = "llm"
    THINKING_LEVEL_CHANGE = "thinking_level_change"
    MODEL_CHANGE = "model_change"
    COMPACTION_SUMMARY = "compaction_summary"
    BRANCH_SUMMARY = "branch_summary"
    LABEL = "label"
    SESSION_INFO = "session_info"
    CUSTOM = "custom"
    CUSTOM_INFO = "custom_info"


# Session Entry Types
@dataclass
class BaseSessionEntry:
    type: SessionEntryType
    id: str
    parent_id: Optional[str]
    timestamp: str

@dataclass
class SessionHeader(BaseSessionEntry):
    type: SessionEntryType = field(default=SessionEntryType.SESSION_HEADER, init=False)
    version: int
    cwd: str
    parent_session_path: Optional[Path] = None

@dataclass
class LLMMessageEntry(BaseSessionEntry):
    type: SessionEntryType = field(default=SessionEntryType.LLM, init=False)
    message: LLMMessage

@dataclass
class ThinkingLevelChangeEntry(BaseSessionEntry):
    type: SessionEntryType = field(default=SessionEntryType.THINKING_LEVEL_CHANGE, init=False)
    thinking_level: ThinkingLevel

@dataclass
class ModelChangeEntry(BaseSessionEntry):
    type: SessionEntryType = field(default=SessionEntryType.MODEL_CHANGE, init=False)
    provider: str
    model_id: str

@dataclass
class CompactionSummaryEntry(BaseSessionEntry):
    type: SessionEntryType = field(default=SessionEntryType.COMPACTION_SUMMARY, init=False)
    summary: str
    first_kept_entry_id: str
    tokens_before: int
    details: Optional[Any] = None

@dataclass
class BranchSummaryEntry(BaseSessionEntry):
    type: SessionEntryType = field(default=SessionEntryType.BRANCH_SUMMARY, init=False)
    from_id: str
    summary: str
    details: Optional[Any] = None

@dataclass
class LabelEntry(BaseSessionEntry):
    type: SessionEntryType = field(default=SessionEntryType.LABEL, init=False)
    target_id: str
    label: Optional[str]

@dataclass
class SessionInfoEntry(BaseSessionEntry):
    type: SessionEntryType = field(default=SessionEntryType.SESSION_INFO, init=False)
    name: Optional[str]

@dataclass
class CustomInfoEntry(BaseSessionEntry):
    type: SessionEntryType = field(default=SessionEntryType.CUSTOM_INFO, init=False)
    custom_type: str
    data: Optional[Any] = None

@dataclass
class CustomMessageEntry(BaseSessionEntry):
    type: SessionEntryType = field(default=SessionEntryType.CUSTOM, init=False)
    custom_type: str
    content: str
    display: bool = False
    details: Optional[Any] = None

@dataclass
class SessionTreeNode:
    """Tree node for getTree() - defensive copy of session structure"""
    entry: SessionEntry
    children: List[SessionTreeNode]
    label: Optional[str] = None
    label_timestamp: Optional[str] = None


@dataclass
class SessionContext:
    """Context built from session entries for sending to LLM"""
    messages: List[LLMMessage]
    thinking_level: str
    model: Optional[Dict[str, str]] = None

SessionEntry = (
    LLMMessageEntry | ThinkingLevelChangeEntry | ModelChangeEntry |
    CompactionSummaryEntry | BranchSummaryEntry | LabelEntry | SessionInfoEntry |
    CustomInfoEntry | CustomMessageEntry
)

FileEntry = SessionHeader | SessionEntry



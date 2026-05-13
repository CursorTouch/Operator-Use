from __future__ import annotations
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from program.message.types import LLMMessage

class SessionEntryType(str,Enum):
    SESSION = "session"
    THINKING_LEVEL_CHANGE = "thinking_level_change"
    MODEL_CHANGE = "model_change"
    COMPACTION = "compaction"
    BRANCH_SUMMARY = "branch_summary"
    LABEL = "label"
    SESSION_HEADER = "session_header"
    SESSION_INFO = "session_info"
    CUSTOM = "custom"
    CUSTOM_INFO = "custom_info"


# Session Entry Types
@dataclass
class BaseSession:
    type: SessionEntryType
    id: str
    parent_id: Optional[str]
    timestamp: str

@dataclass
class SessionHeader(BaseSession):
    type: SessionEntryType = field(default=SessionEntryType.SESSION_HEADER, init=False)
    version: int
    cwd: str
    parent_session: Optional[str] = None

@dataclass
class SessionMessage(BaseSession):
    type: SessionEntryType = field(default=SessionEntryType.SESSION, init=False)
    message: LLMMessage

@dataclass
class ThinkingLevelChange(BaseSession):
    type: SessionEntryType = field(default=SessionEntryType.THINKING_LEVEL_CHANGE, init=False)
    thinking_level: str

@dataclass
class ModelChange(BaseSession):
    type: SessionEntryType = field(default=SessionEntryType.MODEL_CHANGE, init=False)
    provider: str
    model_id: str

@dataclass
class Compaction(BaseSession):
    type: SessionEntryType = field(default=SessionEntryType.COMPACTION, init=False)
    summary: str
    first_kept_entry_id: str
    tokens_before: int
    details: Optional[Any] = None

@dataclass
class BranchSummary(BaseSession):
    type: SessionEntryType = field(default=SessionEntryType.BRANCH_SUMMARY, init=False)
    from_id: str
    summary: str
    details: Optional[Any] = None

@dataclass
class Label(BaseSession):
    type: SessionEntryType = field(default=SessionEntryType.LABEL, init=False)
    target_id: str
    label: Optional[str]

@dataclass
class SessionInfo(BaseSession):
    type: SessionEntryType = field(default=SessionEntryType.SESSION_INFO, init=False)
    name: Optional[str]

@dataclass
class CustomInfo(BaseSession):
    type: SessionEntryType = field(default=SessionEntryType.CUSTOM_INFO, init=False)
    custom_type: str
    data: Optional[Any] = None

@dataclass
class CustomMessage(BaseSession):
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

SessionEntry = (
    SessionMessage | ThinkingLevelChange | ModelChange |
    Compaction | BranchSummary | Label | SessionInfo |
    CustomInfo | CustomMessage
)

Session = SessionHeader | SessionEntry



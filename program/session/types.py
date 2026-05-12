
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

# Session Entry Types
@dataclass
class SessionEntryBase:
    id: str
    parent_id: Optional[str]
    timestamp: str

@dataclass
class SessionHeader(SessionEntryBase):
    version: int
    cwd: str
    type: str = field(default="session", init=False)
    parent_session: Optional[str] = None

@dataclass
class SessionMessageEntry(SessionEntryBase):
    message: Dict[str, Any]
    type: str = field(default="message", init=False)

@dataclass
class ThinkingLevelChangeEntry(SessionEntryBase):
    thinking_level: str
    type: str = field(default="thinking_level_change", init=False)

@dataclass
class ModelChangeEntry(SessionEntryBase):
    provider: str
    model_id: str
    type: str = field(default="model_change", init=False)

@dataclass
class CompactionEntry(SessionEntryBase):
    summary: str
    first_kept_entry_id: str
    tokens_before: int
    type: str = field(default="compaction", init=False)
    details: Optional[Any] = None

@dataclass
class BranchSummaryEntry(SessionEntryBase):
    from_id: str
    summary: str
    type: str = field(default="branch_summary", init=False)
    details: Optional[Any] = None

@dataclass
class LabelEntry(SessionEntryBase):
    target_id: str
    label: Optional[str]
    type: str = field(default="label", init=False)

@dataclass
class SessionInfoEntry(SessionEntryBase):
    name: Optional[str]
    type: str = field(default="session_info", init=False)

@dataclass
class CustomEntry(SessionEntryBase):
    custom_type: str
    type: str = field(default="custom", init=False)
    data: Optional[Any] = None

@dataclass
class CustomMessageEntry(SessionEntryBase):
    content: str
    type: str = field(default="custom_message", init=False)
    display: Optional[str] = None
    details: Optional[Any] = None

@dataclass
class SessionTreeNode:
    """Tree node for getTree() - defensive copy of session structure"""
    entry: "SessionEntry"
    children: List["SessionTreeNode"]
    label: Optional[str] = None
    label_timestamp: Optional[str] = None

SessionEntry = (
    SessionMessageEntry | ThinkingLevelChangeEntry | ModelChangeEntry |
    CompactionEntry | BranchSummaryEntry | LabelEntry | SessionInfoEntry |
    CustomEntry | CustomMessageEntry
)

FileEntry = SessionHeader | SessionEntry

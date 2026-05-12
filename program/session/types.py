
from dataclasses import dataclass  
from typing import Optional, Dict, Any, List

# Session Entry Types  
@dataclass  
class SessionEntryBase:  
    type: str  
    id: str  
    parent_id: Optional[str]  
    timestamp: str  
  
@dataclass  
class SessionHeader(SessionEntryBase):  
    version: int  
    cwd: str  
    parent_session: Optional[str] = None  
  
@dataclass  
class SessionMessageEntry(SessionEntryBase):  
    message: Dict[str, Any]  
  
@dataclass  
class ThinkingLevelChangeEntry(SessionEntryBase):  
    thinking_level: str  
  
@dataclass  
class ModelChangeEntry(SessionEntryBase):  
    provider: str  
    model_id: str  
  
@dataclass  
class CompactionEntry(SessionEntryBase):  
    summary: str  
    first_kept_entry_id: str  
    tokens_before: int  
    details: Optional[Any] = None  
  
@dataclass  
class BranchSummaryEntry(SessionEntryBase):  
    from_id: str  
    summary: str  
    details: Optional[Any] = None   
  
@dataclass  
class LabelEntry(SessionEntryBase):  
    target_id: str  
    label: Optional[str]  
  
@dataclass  
class SessionInfoEntry(SessionEntryBase):  
    name: Optional[str]  
  
@dataclass  
class CustomEntry(SessionEntryBase):  
    custom_type: str  
    data: Optional[Any] = None  
  
@dataclass  
class CustomMessageEntry(SessionEntryBase):  
    content: str  
    display: Optional[str] = None  
    details: Optional[Any] = None  

@dataclass  
class SessionTreeNode:  
    """Tree node for getTree() - defensive copy of session structure"""  
    entry: SessionEntry  
    children: List['SessionTreeNode']  
    label: Optional[str] = None  
    label_timestamp: Optional[str] = None
  
SessionEntry = (  
    SessionMessageEntry | ThinkingLevelChangeEntry | ModelChangeEntry |  
    CompactionEntry | BranchSummaryEntry | LabelEntry | SessionInfoEntry |  
    CustomEntry | CustomMessageEntry  
)  
  
FileEntry = SessionHeader | SessionEntry  
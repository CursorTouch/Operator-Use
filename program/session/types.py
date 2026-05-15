from __future__ import annotations
from program.session.types import generate_timestamp
from program.llm.provider.registry import Provider
from program.llm.model import Model
from program.message.types import AgentMessage
from program.session.utils import generate_id
from pydantic import BaseModel, Field, ConfigDict
from program.llm.types import ThinkingLevel
from program.message.types import ImageContent, TextContent
from datetime import datetime
from pathlib import Path
from enum import Enum
from dataclasses import dataclass
from typing import Any, Literal, Annotated

SESSION_VERSION=1.0

class SessionType(str,Enum):
    SESSION_HEADER="session_header"
    SESSION_MESSAGE="session_message"
    THINKING_LEVEL_CHANGE="thinking_level_change"
    MODEL_CHANGE="model_change"
    COMPACTION="compaction"
    BRANCH="branch"
    LABEL="label"
    CUSTOM_INFO="custom_info"
    SESSION_INFO="session_info"
    CUSTOM_MESSAGE="custom_message"

class BaseSessionEntry(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    id: str = Field(default_factory=generate_id)
    timestamp: float = Field(default_factory=generate_timestamp)
    parent_id: str = ""

class SessionHeader(BaseSessionEntry):
    type: Literal[SessionType.SESSION_HEADER] = Field(SessionType.SESSION_HEADER, init=False)
    version: str = str(SESSION_VERSION)
    cwd: Path
    parent_session: Path | None = None

class SessionInfoEntry(BaseSessionEntry):
    type: Literal[SessionType.SESSION_INFO] = Field(SessionType.SESSION_INFO, init=False)
    name: str | None = None

class MessageEntry(BaseSessionEntry):
    type: Literal[SessionType.SESSION_MESSAGE] = Field(SessionType.SESSION_MESSAGE, init=False)
    message: AgentMessage

class ThinkingLevelChangeEntry(BaseSessionEntry):
    type: Literal[SessionType.THINKING_LEVEL_CHANGE] = Field(SessionType.THINKING_LEVEL_CHANGE, init=False)
    thinking_level: ThinkingLevel

class ModelChangeEntry(BaseSessionEntry):
    type: Literal[SessionType.MODEL_CHANGE] = Field(SessionType.MODEL_CHANGE, init=False)
    model: Model
    provider: Provider

class CompactionEntry(BaseSessionEntry):
    type: Literal[SessionType.COMPACTION] = Field(SessionType.COMPACTION, init=False)
    summary: str
    retained_from_id: str
    tokens_before: int
    details: Any | None = None

class BranchEntry(BaseSessionEntry):
    type: Literal[SessionType.BRANCH] = Field(SessionType.BRANCH, init=False)
    from_id: str
    summary: str
    details: Any | None = None

class LabelEntry(BaseSessionEntry):
    type: Literal[SessionType.LABEL] = Field(SessionType.LABEL, init=False)
    label: str | None = None
    target_id: str
    
class CustomInfoEntry(BaseSessionEntry):
    type: Literal[SessionType.CUSTOM_INFO] = Field(SessionType.CUSTOM_INFO, init=False)
    custom_type: str
    data: Any | None = None

class CustomMessageEntry(BaseSessionEntry):
    type: Literal[SessionType.CUSTOM_MESSAGE] = Field(SessionType.CUSTOM_MESSAGE, init=False)
    custom_type: str
    contents: list[str | ImageContent | TextContent]
    details: Any | None = None

SessionEntries= SessionInfoEntry | MessageEntry | ThinkingLevelChangeEntry | ModelChangeEntry | CompactionEntry | BranchEntry | LabelEntry | CustomInfoEntry | CustomMessageEntry

SessionEntry = Annotated[
    SessionEntries,
    Field(discriminator="type")
]

SessionFileEntry = Annotated[
    SessionHeader | SessionEntries,
    Field(discriminator="type")
]

class SessionTreeNode(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    entry: SessionEntry
    children: list[SessionTreeNode] = Field(default_factory=list)
    label: str | None = None
    timestamp: float

class SessionContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    messages: list[AgentMessage]
    thinking_level: ThinkingLevel
    model: Model|None=None
    provider: Provider|None=None

class SessionInfo(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    path: Path
    id: str
    cwd: Path
    name: str | None
    parent_session: Path | None = None
    created: datetime
    modified: datetime
    message_count: int

@dataclass
class SessionOptions:
    id:str|None=None
    parent_session: str | None=None

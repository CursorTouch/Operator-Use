from __future__ import annotations
import uuid
from datetime import datetime
from pathlib import Path
from enum import Enum
from dataclasses import dataclass
from typing import Any, Literal, Annotated, TYPE_CHECKING

from pydantic import BaseModel, Field, ConfigDict
from program.llm.types import ThinkingLevel

if TYPE_CHECKING:
    from program.message.types import AgentMessage, ImageContent, TextContent


def generate_timestamp() -> float:
    return datetime.now().timestamp()


def _generate_id() -> str:
    return str(uuid.uuid4())[:8]


SESSION_VERSION = 3


class SessionType(str, Enum):
    SESSION_HEADER = "session"
    SESSION_MESSAGE = "message"
    THINKING_LEVEL_CHANGE = "thinking_level_change"
    MODEL_CHANGE = "model_change"
    COMPACTION = "compaction"
    BRANCH = "branch_summary"
    LABEL = "label"
    CUSTOM_INFO = "custom"
    SESSION_INFO = "session_info"
    CUSTOM_MESSAGE = "custom_message"


class BaseSessionEntry(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    id: str = Field(default_factory=_generate_id)
    timestamp: float = Field(default_factory=generate_timestamp)
    parent_id: str | None = None


class SessionHeader(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    type: Literal[SessionType.SESSION_HEADER] = Field(SessionType.SESSION_HEADER, init=False)
    version: int = SESSION_VERSION
    id: str = Field(default_factory=_generate_id)
    timestamp: float = Field(default_factory=generate_timestamp)
    cwd: Path
    parent_session: Path | None = None


class SessionInfoEntry(BaseSessionEntry):
    type: Literal[SessionType.SESSION_INFO] = Field(SessionType.SESSION_INFO, init=False)
    name: str | None = None


class MessageEntry(BaseSessionEntry):
    type: Literal[SessionType.SESSION_MESSAGE] = Field(SessionType.SESSION_MESSAGE, init=False)
    message: "AgentMessage"


class ThinkingLevelChangeEntry(BaseSessionEntry):
    type: Literal[SessionType.THINKING_LEVEL_CHANGE] = Field(SessionType.THINKING_LEVEL_CHANGE, init=False)
    thinking_level: ThinkingLevel


class ModelChangeEntry(BaseSessionEntry):
    type: Literal[SessionType.MODEL_CHANGE] = Field(SessionType.MODEL_CHANGE, init=False)
    model_id: str
    provider_id: str


class CompactionEntry(BaseSessionEntry):
    type: Literal[SessionType.COMPACTION] = Field(SessionType.COMPACTION, init=False)
    summary: str
    first_kept_entry_id: str
    tokens_before: int
    details: Any | None = None
    from_hook: bool = False


class BranchEntry(BaseSessionEntry):
    type: Literal[SessionType.BRANCH] = Field(SessionType.BRANCH, init=False)
    from_id: str
    summary: str
    details: Any | None = None
    from_hook: bool = False


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
    content: list["TextContent | ImageContent"]
    display: bool = True
    details: Any | None = None


SessionEntries = (
    SessionInfoEntry
    | MessageEntry
    | ThinkingLevelChangeEntry
    | ModelChangeEntry
    | CompactionEntry
    | BranchEntry
    | LabelEntry
    | CustomInfoEntry
    | CustomMessageEntry
)

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
    label_timestamp: float | None = None


class SessionContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    messages: list["AgentMessage"]
    thinking_level: ThinkingLevel
    model_id: str | None = None
    provider_id: str | None = None


class SessionInfo(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    path: Path
    id: str
    cwd: Path
    name: str | None = None
    parent_session: Path | None = None
    created: datetime
    modified: datetime
    message_count: int


@dataclass
class SessionOptions:
    id: str | None = None
    parent_session: str | None = None

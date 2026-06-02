from __future__ import annotations
import uuid
from datetime import datetime
from pathlib import Path
from enum import Enum
from dataclasses import dataclass
from typing import Any, Literal, Annotated, TYPE_CHECKING

from pydantic import BaseModel, Field, ConfigDict
from operator_use.inference.types import ThinkingLevel

from operator_use.message.types import AgentMessage, ImageContent, TextContent

if TYPE_CHECKING:
    pass


def generate_timestamp() -> float:
    """Return the current wall-clock time as a UNIX timestamp float."""
    return datetime.now().timestamp()


def _generate_id() -> str:
    """Generate a random 8-character hex string for use as a short entry ID."""
    return str(uuid.uuid4())[:8]


SESSION_VERSION = 3


class SessionType(str, Enum):
    """Discriminator literals written into every JSONL entry's `type` field."""

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
    LEAF = "leaf"
    CHANNEL = "channel"


class BaseSessionEntry(BaseModel):
    """Shared fields for every non-header entry in the session linked list."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    id: str = Field(default_factory=_generate_id)
    timestamp: float = Field(default_factory=generate_timestamp)
    parent_id: str | None = None


class SessionHeader(BaseModel):
    """First line of every session JSONL file; identifies the session and its working directory."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    type: Literal[SessionType.SESSION_HEADER] = SessionType.SESSION_HEADER
    version: int = SESSION_VERSION
    id: str = Field(default_factory=_generate_id)
    timestamp: float = Field(default_factory=generate_timestamp)
    cwd: Path
    parent_session: Path | None = None


class SessionInfoEntry(BaseSessionEntry):
    """Records a human-readable session name at a point in the branch."""

    type: Literal[SessionType.SESSION_INFO] = SessionType.SESSION_INFO
    name: str | None = None


class ChannelEntry(BaseSessionEntry):
    """Records which gateway channel (and optional chat/user) is active at this branch point."""

    type: Literal[SessionType.CHANNEL] = SessionType.CHANNEL
    name: str
    chat_id: str | None = None
    user_id: str | None = None


class MessageAttachment(BaseModel):
    """A file attachment associated with a message, stored by path and optional MIME type."""

    path: str
    mime_type: str | None = None


class MessageMeta(BaseModel):
    """Side-channel metadata attached to a MessageEntry that is not part of the LLM turn."""

    reply_to: str | None = None                      # message_id this is replying to
    channel_message_id: str | None = None            # channel-side message ID (for reaction lookup)
    attachments: list[MessageAttachment] | None = None
    reactions: list[str] | None = None


class MessageEntry(BaseSessionEntry):
    """Wraps a single AgentMessage (user, assistant, tool-call, etc.) in the session log."""

    type: Literal[SessionType.SESSION_MESSAGE] = SessionType.SESSION_MESSAGE
    message: Annotated["AgentMessage", Field(discriminator="role")]
    meta: MessageMeta | None = None


class ThinkingLevelChangeEntry(BaseSessionEntry):
    """Records a change to the model's thinking/reasoning level at this branch point."""

    type: Literal[SessionType.THINKING_LEVEL_CHANGE] = SessionType.THINKING_LEVEL_CHANGE
    thinking_level: ThinkingLevel


class ModelChangeEntry(BaseSessionEntry):
    """Records a switch to a different model or provider at this branch point."""

    type: Literal[SessionType.MODEL_CHANGE] = SessionType.MODEL_CHANGE
    model_id: str
    provider_id: str


class CompactionEntry(BaseSessionEntry):
    """Marks where context was compacted; entries before `first_kept_entry_id` are replaced by `summary`."""

    type: Literal[SessionType.COMPACTION] = SessionType.COMPACTION
    summary: str
    first_kept_entry_id: str
    tokens_before: int
    details: Any | None = None
    from_hook: bool = False


class BranchEntry(BaseSessionEntry):
    """A branch-point marker that carries a prose summary of the diverged history."""

    type: Literal[SessionType.BRANCH] = SessionType.BRANCH
    from_id: str
    summary: str
    details: Any | None = None
    from_hook: bool = False


class LabelEntry(BaseSessionEntry):
    """Attaches or removes a human-readable label from an existing entry by target_id."""

    type: Literal[SessionType.LABEL] = SessionType.LABEL
    label: str | None = None  # None means "remove label"
    target_id: str


class LeafEntry(BaseSessionEntry):
    """Navigation record: persists a branch() call so the active leaf survives restarts."""

    type: Literal[SessionType.LEAF] = SessionType.LEAF
    target_id: str | None = None


class CustomInfoEntry(BaseSessionEntry):
    """Extension-defined structured metadata entry that does not produce an LLM message."""

    type: Literal[SessionType.CUSTOM_INFO] = SessionType.CUSTOM_INFO
    custom_type: str
    data: Any | None = None


class CustomMessageEntry(BaseSessionEntry):
    """Extension-defined displayable message entry injected into the conversation context."""

    type: Literal[SessionType.CUSTOM_MESSAGE] = SessionType.CUSTOM_MESSAGE
    custom_type: str
    content: list["TextContent | ImageContent"]
    display: bool = True
    details: Any | None = None


SessionEntries = (
    SessionInfoEntry
    | ChannelEntry
    | MessageEntry
    | ThinkingLevelChangeEntry
    | ModelChangeEntry
    | CompactionEntry
    | BranchEntry
    | LabelEntry
    | LeafEntry
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
    """One node in the full session DAG returned by SessionManager.get_tree()."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    entry: SessionEntry
    children: list[SessionTreeNode] = Field(default_factory=list)
    label: str | None = None
    label_timestamp: float | None = None


class SessionContext(BaseModel):
    """Reconstructed LLM-ready context derived from the active branch of a session."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    messages: list["AgentMessage"]
    thinking_level: ThinkingLevel
    model_id: str | None = None
    provider_id: str | None = None


class SessionInfo(BaseModel):
    """Summary metadata for a session file used in listing and display."""

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
    """Optional overrides supplied when creating a new session."""

    id: str | None = None
    parent_session: str | None = None

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional


# ============================================================================
# Command types
# ============================================================================

class CommandType(str, Enum):
    # Prompting
    Prompt          = "prompt"
    Steer           = "steer"
    FollowUp        = "follow_up"
    Abort           = "abort"
    # Session management
    NewSession      = "new_session"
    SwitchSession   = "switch_session"
    Fork            = "fork"
    SetSessionName  = "set_session_name"
    # State queries
    GetState        = "get_state"
    GetMessages     = "get_messages"
    GetLastAssistantText = "get_last_assistant_text"
    GetSessionStats = "get_session_stats"
    # Model / thinking
    SetModel        = "set_model"
    SetThinkingLevel = "set_thinking_level"
    # Compaction
    Compact         = "compact"
    SetAutoCompaction = "set_auto_compaction"
    # Extension UI
    ExtensionUIResponse = "extension_ui_response"
    ExtensionUIRequest  = "extension_ui_request"
    # Internal
    Response        = "response"


# ============================================================================
# Command payloads (Client → Server)
# ============================================================================

@dataclass
class PromptCommand:
    message: str
    id: Optional[str] = None
    type: str = field(default=CommandType.Prompt, init=False)


@dataclass
class SteerCommand:
    message: str
    id: Optional[str] = None
    type: str = field(default=CommandType.Steer, init=False)


@dataclass
class FollowUpCommand:
    message: str
    id: Optional[str] = None
    type: str = field(default=CommandType.FollowUp, init=False)


@dataclass
class AbortCommand:
    id: Optional[str] = None
    type: str = field(default=CommandType.Abort, init=False)


@dataclass
class NewSessionCommand:
    id: Optional[str] = None
    type: str = field(default=CommandType.NewSession, init=False)


@dataclass
class SwitchSessionCommand:
    session_path: str = ""
    id: Optional[str] = None
    type: str = field(default=CommandType.SwitchSession, init=False)


@dataclass
class ForkCommand:
    entry_id: str = ""
    id: Optional[str] = None
    type: str = field(default=CommandType.Fork, init=False)


@dataclass
class SetSessionNameCommand:
    name: str = ""
    id: Optional[str] = None
    type: str = field(default=CommandType.SetSessionName, init=False)


@dataclass
class GetStateCommand:
    id: Optional[str] = None
    type: str = field(default=CommandType.GetState, init=False)


@dataclass
class GetMessagesCommand:
    id: Optional[str] = None
    type: str = field(default=CommandType.GetMessages, init=False)


@dataclass
class GetLastAssistantTextCommand:
    id: Optional[str] = None
    type: str = field(default=CommandType.GetLastAssistantText, init=False)


@dataclass
class GetSessionStatsCommand:
    id: Optional[str] = None
    type: str = field(default=CommandType.GetSessionStats, init=False)


@dataclass
class SetModelCommand:
    model_id: str = ""
    provider: Optional[str] = None
    id: Optional[str] = None
    type: str = field(default=CommandType.SetModel, init=False)


@dataclass
class SetThinkingLevelCommand:
    level: str = ""
    id: Optional[str] = None
    type: str = field(default=CommandType.SetThinkingLevel, init=False)


@dataclass
class CompactCommand:
    custom_instructions: Optional[str] = None
    id: Optional[str] = None
    type: str = field(default=CommandType.Compact, init=False)


@dataclass
class SetAutoCompactionCommand:
    enabled: bool = True
    id: Optional[str] = None
    type: str = field(default=CommandType.SetAutoCompaction, init=False)


@dataclass
class ExtensionUIResponseCommand:
    id: str = ""
    value: Any = None
    cancelled: bool = False
    type: str = field(default=CommandType.ExtensionUIResponse, init=False)


# Union of all commands
RPCCommand = (
    PromptCommand
    | SteerCommand
    | FollowUpCommand
    | AbortCommand
    | NewSessionCommand
    | SwitchSessionCommand
    | ForkCommand
    | SetSessionNameCommand
    | GetStateCommand
    | GetMessagesCommand
    | GetLastAssistantTextCommand
    | GetSessionStatsCommand
    | SetModelCommand
    | SetThinkingLevelCommand
    | CompactCommand
    | SetAutoCompactionCommand
    | ExtensionUIResponseCommand
)


# ============================================================================
# Response payloads (Server → Client)
# ============================================================================

@dataclass
class OkResponse:
    command: str
    id: Optional[str] = None
    data: Any = None
    type: Literal["response"] = field(default="response", init=False)
    success: Literal[True] = field(default=True, init=False)


@dataclass
class ErrorResponse:
    command: str
    error: str
    id: Optional[str] = None
    type: Literal["response"] = field(default="response", init=False)
    success: Literal[False] = field(default=False, init=False)


# ============================================================================
# Extension UI sub-protocol (Server → Client round-trip)
# ============================================================================

@dataclass
class ExtensionUIRequest:
    """Sent by the server to the host to request user input."""
    id: str
    method: str                          # "confirm" | "select" | "input" | "notify"
    fields: dict[str, Any] = field(default_factory=dict)
    type: Literal["extension_ui_request"] = field(default="extension_ui_request", init=False)


# ============================================================================
# Event shapes emitted by the server (Server → Client, streamed)
# ============================================================================

@dataclass
class RPCEvent:
    """Generic wrapper for any agent/extension event sent over the wire."""
    type: str
    data: dict[str, Any] = field(default_factory=dict)

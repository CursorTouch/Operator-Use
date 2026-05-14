from __future__ import annotations
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Literal, Optional, Type, Union

from program.compaction.types import CompactionPreparation, CompactionResult
from program.llm.model.types import Model
from program.llm.types import ThinkingLevel
from program.message.types import BaseMessage, ImageContent, TextContent
from program.session.types import (
    BranchSummaryEntry,
    CompactionSummaryEntry,
    SessionEntry,
)
from program.tool.types import ToolExecutionMode, ToolInvocation, ToolResult

AbortSignal = asyncio.Event

# ---------------------------------------------------------------------------
# Stubs for non-UI types not yet implemented
# ---------------------------------------------------------------------------
ReadonlySessionManager = Any
SessionManager = Any
ModelRegistry = Any
SlashCommandInfo = Any
BashOperations = Any
BashResult = Any
SourceInfo = Any
BuildSystemPromptOptions = Any


# ---------------------------------------------------------------------------
# Extension Context
# ---------------------------------------------------------------------------

@dataclass
class ContextUsage:
    tokens: Optional[int]
    context_window: int
    percent: Optional[float]


@dataclass
class CompactOptions:
    custom_instructions: Optional[str] = None
    on_complete: Optional[Callable[[CompactionResult], None]] = None
    on_error: Optional[Callable[[Exception], None]] = None


class ExtensionContext(ABC):
    @property
    @abstractmethod
    def cwd(self) -> str: ...
    @property
    @abstractmethod
    def session_manager(self) -> ReadonlySessionManager: ...
    @property
    @abstractmethod
    def model_registry(self) -> ModelRegistry: ...
    @property
    @abstractmethod
    def model(self) -> Optional[Model]: ...
    @abstractmethod
    def is_idle(self) -> bool: ...
    @property
    @abstractmethod
    def signal(self) -> Optional[AbortSignal]: ...
    @abstractmethod
    def abort(self) -> None: ...
    @abstractmethod
    def has_pending_messages(self) -> bool: ...
    @abstractmethod
    def shutdown(self) -> None: ...
    @abstractmethod
    def get_context_usage(self) -> Optional[ContextUsage]: ...
    @abstractmethod
    def compact(self, options: Optional[CompactOptions] = None) -> None: ...
    @abstractmethod
    def get_system_prompt(self) -> str: ...


@dataclass
class ReplacedSessionContext:
    ctx: ExtensionCommandContext

    async def send_message(self, message: dict, options: Optional[dict] = None) -> None: ...
    async def send_user_message(self, content: Union[str, list], options: Optional[dict] = None) -> None: ...


class ExtensionCommandContext(ExtensionContext):
    @abstractmethod
    async def wait_for_idle(self) -> None: ...
    @abstractmethod
    async def new_session(self, options: Optional[dict] = None) -> dict: ...
    @abstractmethod
    async def fork(self, entry_id: str, options: Optional[dict] = None) -> dict: ...
    @abstractmethod
    async def navigate_tree(self, target_id: str, options: Optional[dict] = None) -> dict: ...
    @abstractmethod
    async def switch_session(self, session_path: str, options: Optional[dict] = None) -> dict: ...
    @abstractmethod
    async def reload(self) -> None: ...


# ---------------------------------------------------------------------------
# Tool Types
# ---------------------------------------------------------------------------

@dataclass
class ToolInfo:
    name: str
    description: str
    parameters: Any
    source: Optional[SourceInfo] = None


@dataclass
class ToolDefinition:
    name: str
    label: str
    description: str
    parameters: Type[Any]
    execute: Callable[..., Awaitable[ToolResult]]
    prompt_snippet: Optional[str] = None
    prompt_guidelines: Optional[list[str]] = None
    prepare_arguments: Optional[Callable[[Any], Any]] = None
    execution_mode: Optional[ToolExecutionMode] = None


# ---------------------------------------------------------------------------
# Command Registration
# ---------------------------------------------------------------------------

@dataclass
class RegisteredCommand:
    name: str
    handler: Callable[[str, ExtensionCommandContext], Awaitable[None]]
    source_info: Optional[SourceInfo] = None
    description: Optional[str] = None
    get_argument_completions: Optional[Callable[[str], Any]] = None


@dataclass
class ResolvedCommand(RegisteredCommand):
    invocation_name: str = ""


# ---------------------------------------------------------------------------
# Resource Events
# ---------------------------------------------------------------------------

@dataclass
class ResourcesDiscoverEvent:
    type: Literal["resources_discover"] = field(default="resources_discover", init=False)
    cwd: str = ""
    reason: Literal["startup", "reload"] = "startup"


@dataclass
class ResourcesDiscoverResult:
    skill_paths: Optional[list[str]] = None
    prompt_paths: Optional[list[str]] = None


# ---------------------------------------------------------------------------
# Session Events
# ---------------------------------------------------------------------------

@dataclass
class SessionStartEvent:
    type: Literal["session_start"] = field(default="session_start", init=False)
    reason: Literal["startup", "reload", "new", "resume", "fork"] = "startup"
    previous_session_file: Optional[str] = None


@dataclass
class SessionBeforeSwitchEvent:
    type: Literal["session_before_switch"] = field(default="session_before_switch", init=False)
    reason: Literal["new", "resume"] = "new"
    target_session_file: Optional[str] = None


@dataclass
class SessionBeforeForkEvent:
    type: Literal["session_before_fork"] = field(default="session_before_fork", init=False)
    entry_id: str = ""
    position: Literal["before", "at"] = "at"


@dataclass
class SessionBeforeCompactEvent:
    type: Literal["session_before_compact"] = field(default="session_before_compact", init=False)
    preparation: Optional[CompactionPreparation] = None
    branch_entries: list[SessionEntry] = field(default_factory=list)
    custom_instructions: Optional[str] = None
    signal: Optional[AbortSignal] = None


@dataclass
class SessionCompactEvent:
    type: Literal["session_compact"] = field(default="session_compact", init=False)
    compaction_entry: Optional[CompactionSummaryEntry] = None
    from_extension: bool = False


@dataclass
class SessionShutdownEvent:
    type: Literal["session_shutdown"] = field(default="session_shutdown", init=False)
    reason: Literal["quit", "reload", "new", "resume", "fork"] = "quit"
    target_session_file: Optional[str] = None


@dataclass
class TreePreparation:
    target_id: str
    old_leaf_id: Optional[str]
    common_ancestor_id: Optional[str]
    entries_to_summarize: list[SessionEntry]
    user_wants_summary: bool
    custom_instructions: Optional[str] = None
    replace_instructions: Optional[bool] = None
    label: Optional[str] = None


@dataclass
class SessionBeforeTreeEvent:
    type: Literal["session_before_tree"] = field(default="session_before_tree", init=False)
    preparation: Optional[TreePreparation] = None
    signal: Optional[AbortSignal] = None


@dataclass
class SessionTreeEvent:
    type: Literal["session_tree"] = field(default="session_tree", init=False)
    new_leaf_id: Optional[str] = None
    old_leaf_id: Optional[str] = None
    summary_entry: Optional[BranchSummaryEntry] = None
    from_extension: Optional[bool] = None


SessionEvent = Union[
    SessionStartEvent,
    SessionBeforeSwitchEvent,
    SessionBeforeForkEvent,
    SessionBeforeCompactEvent,
    SessionCompactEvent,
    SessionShutdownEvent,
    SessionBeforeTreeEvent,
    SessionTreeEvent,
]

# ---------------------------------------------------------------------------
# Agent Events
# ---------------------------------------------------------------------------

@dataclass
class ContextEvent:
    type: Literal["context"] = field(default="context", init=False)
    messages: list[BaseMessage] = field(default_factory=list)


@dataclass
class BeforeProviderRequestEvent:
    type: Literal["before_provider_request"] = field(default="before_provider_request", init=False)
    payload: Any = None


@dataclass
class AfterProviderResponseEvent:
    type: Literal["after_provider_response"] = field(default="after_provider_response", init=False)
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class BeforeAgentStartEvent:
    type: Literal["before_agent_start"] = field(default="before_agent_start", init=False)
    prompt: str = ""
    images: Optional[list[ImageContent]] = None
    system_prompt: str = ""
    system_prompt_options: Optional[BuildSystemPromptOptions] = None


@dataclass
class AgentStartEvent:
    type: Literal["agent_start"] = field(default="agent_start", init=False)


@dataclass
class AgentEndEvent:
    type: Literal["agent_end"] = field(default="agent_end", init=False)
    messages: list[BaseMessage] = field(default_factory=list)


@dataclass
class TurnStartEvent:
    type: Literal["turn_start"] = field(default="turn_start", init=False)
    turn_index: int = 0
    timestamp: float = 0.0


@dataclass
class TurnEndEvent:
    type: Literal["turn_end"] = field(default="turn_end", init=False)
    turn_index: int = 0
    message: Optional[BaseMessage] = None
    tool_results: list[Any] = field(default_factory=list)


@dataclass
class MessageStartEvent:
    type: Literal["message_start"] = field(default="message_start", init=False)
    message: Optional[BaseMessage] = None


@dataclass
class MessageUpdateEvent:
    type: Literal["message_update"] = field(default="message_update", init=False)
    message: Optional[BaseMessage] = None
    assistant_message_event: Optional[Any] = None


@dataclass
class MessageEndEvent:
    type: Literal["message_end"] = field(default="message_end", init=False)
    message: Optional[BaseMessage] = None


@dataclass
class ToolExecutionStartEvent:
    type: Literal["tool_execution_start"] = field(default="tool_execution_start", init=False)
    tool_call_id: str = ""
    tool_name: str = ""
    args: Any = None


@dataclass
class ToolExecutionUpdateEvent:
    type: Literal["tool_execution_update"] = field(default="tool_execution_update", init=False)
    tool_call_id: str = ""
    tool_name: str = ""
    args: Any = None
    partial_result: Any = None


@dataclass
class ToolExecutionEndEvent:
    type: Literal["tool_execution_end"] = field(default="tool_execution_end", init=False)
    tool_call_id: str = ""
    tool_name: str = ""
    result: Any = None
    is_error: bool = False


# ---------------------------------------------------------------------------
# Model Events
# ---------------------------------------------------------------------------

ModelSelectSource = Literal["set", "cycle", "restore"]


@dataclass
class ModelSelectEvent:
    type: Literal["model_select"] = field(default="model_select", init=False)
    model: Optional[Model] = None
    previous_model: Optional[Model] = None
    source: ModelSelectSource = "set"


@dataclass
class ThinkingLevelSelectEvent:
    type: Literal["thinking_level_select"] = field(default="thinking_level_select", init=False)
    level: ThinkingLevel = ThinkingLevel.Medium
    previous_level: ThinkingLevel = ThinkingLevel.Medium


# ---------------------------------------------------------------------------
# User Bash Events
# ---------------------------------------------------------------------------

@dataclass
class UserBashEvent:
    type: Literal["user_bash"] = field(default="user_bash", init=False)
    command: str = ""
    exclude_from_context: bool = False
    cwd: str = ""


# ---------------------------------------------------------------------------
# Input Events
# ---------------------------------------------------------------------------

InputSource = Literal["interactive", "rpc", "extension"]


@dataclass
class InputEvent:
    type: Literal["input"] = field(default="input", init=False)
    text: str = ""
    images: Optional[list[ImageContent]] = None
    source: InputSource = "interactive"


@dataclass
class InputEventContinueResult:
    action: Literal["continue"] = field(default="continue", init=False)


@dataclass
class InputEventTransformResult:
    action: Literal["transform"] = field(default="transform", init=False)
    text: str = ""
    images: Optional[list[ImageContent]] = None


@dataclass
class InputEventHandledResult:
    action: Literal["handled"] = field(default="handled", init=False)


InputEventResult = Union[InputEventContinueResult, InputEventTransformResult, InputEventHandledResult]

# ---------------------------------------------------------------------------
# Tool Call Events
# ---------------------------------------------------------------------------

@dataclass
class BashToolCallEvent:
    type: Literal["tool_call"] = field(default="tool_call", init=False)
    tool_name: Literal["bash"] = field(default="bash", init=False)
    tool_call_id: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReadToolCallEvent:
    type: Literal["tool_call"] = field(default="tool_call", init=False)
    tool_name: Literal["read"] = field(default="read", init=False)
    tool_call_id: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class EditToolCallEvent:
    type: Literal["tool_call"] = field(default="tool_call", init=False)
    tool_name: Literal["edit"] = field(default="edit", init=False)
    tool_call_id: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class WriteToolCallEvent:
    type: Literal["tool_call"] = field(default="tool_call", init=False)
    tool_name: Literal["write"] = field(default="write", init=False)
    tool_call_id: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class GrepToolCallEvent:
    type: Literal["tool_call"] = field(default="tool_call", init=False)
    tool_name: Literal["grep"] = field(default="grep", init=False)
    tool_call_id: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class FindToolCallEvent:
    type: Literal["tool_call"] = field(default="tool_call", init=False)
    tool_name: Literal["find"] = field(default="find", init=False)
    tool_call_id: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class LsToolCallEvent:
    type: Literal["tool_call"] = field(default="tool_call", init=False)
    tool_name: Literal["ls"] = field(default="ls", init=False)
    tool_call_id: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class CustomToolCallEvent:
    type: Literal["tool_call"] = field(default="tool_call", init=False)
    tool_name: str = ""
    tool_call_id: str = ""
    input: dict[str, Any] = field(default_factory=dict)


ToolCallEvent = Union[
    BashToolCallEvent,
    ReadToolCallEvent,
    EditToolCallEvent,
    WriteToolCallEvent,
    GrepToolCallEvent,
    FindToolCallEvent,
    LsToolCallEvent,
    CustomToolCallEvent,
]

# ---------------------------------------------------------------------------
# Tool Result Events
# ---------------------------------------------------------------------------

@dataclass
class BashToolResultEvent:
    type: Literal["tool_result"] = field(default="tool_result", init=False)
    tool_name: Literal["bash"] = field(default="bash", init=False)
    tool_call_id: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    content: list[Union[TextContent, ImageContent]] = field(default_factory=list)
    is_error: bool = False
    details: Optional[Any] = None


@dataclass
class ReadToolResultEvent:
    type: Literal["tool_result"] = field(default="tool_result", init=False)
    tool_name: Literal["read"] = field(default="read", init=False)
    tool_call_id: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    content: list[Union[TextContent, ImageContent]] = field(default_factory=list)
    is_error: bool = False
    details: Optional[Any] = None


@dataclass
class EditToolResultEvent:
    type: Literal["tool_result"] = field(default="tool_result", init=False)
    tool_name: Literal["edit"] = field(default="edit", init=False)
    tool_call_id: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    content: list[Union[TextContent, ImageContent]] = field(default_factory=list)
    is_error: bool = False
    details: Optional[Any] = None


@dataclass
class WriteToolResultEvent:
    type: Literal["tool_result"] = field(default="tool_result", init=False)
    tool_name: Literal["write"] = field(default="write", init=False)
    tool_call_id: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    content: list[Union[TextContent, ImageContent]] = field(default_factory=list)
    is_error: bool = False
    details: None = None


@dataclass
class GrepToolResultEvent:
    type: Literal["tool_result"] = field(default="tool_result", init=False)
    tool_name: Literal["grep"] = field(default="grep", init=False)
    tool_call_id: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    content: list[Union[TextContent, ImageContent]] = field(default_factory=list)
    is_error: bool = False
    details: Optional[Any] = None


@dataclass
class FindToolResultEvent:
    type: Literal["tool_result"] = field(default="tool_result", init=False)
    tool_name: Literal["find"] = field(default="find", init=False)
    tool_call_id: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    content: list[Union[TextContent, ImageContent]] = field(default_factory=list)
    is_error: bool = False
    details: Optional[Any] = None


@dataclass
class LsToolResultEvent:
    type: Literal["tool_result"] = field(default="tool_result", init=False)
    tool_name: Literal["ls"] = field(default="ls", init=False)
    tool_call_id: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    content: list[Union[TextContent, ImageContent]] = field(default_factory=list)
    is_error: bool = False
    details: Optional[Any] = None


@dataclass
class CustomToolResultEvent:
    type: Literal["tool_result"] = field(default="tool_result", init=False)
    tool_name: str = ""
    tool_call_id: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    content: list[Union[TextContent, ImageContent]] = field(default_factory=list)
    is_error: bool = False
    details: Any = None


ToolResultEvent = Union[
    BashToolResultEvent,
    ReadToolResultEvent,
    EditToolResultEvent,
    WriteToolResultEvent,
    GrepToolResultEvent,
    FindToolResultEvent,
    LsToolResultEvent,
    CustomToolResultEvent,
]

# ---------------------------------------------------------------------------
# Tool call / result type guards
# ---------------------------------------------------------------------------

def is_bash_tool_result(e: ToolResultEvent) -> bool:
    return e.tool_name == "bash"

def is_read_tool_result(e: ToolResultEvent) -> bool:
    return e.tool_name == "read"

def is_edit_tool_result(e: ToolResultEvent) -> bool:
    return e.tool_name == "edit"

def is_write_tool_result(e: ToolResultEvent) -> bool:
    return e.tool_name == "write"

def is_grep_tool_result(e: ToolResultEvent) -> bool:
    return e.tool_name == "grep"

def is_find_tool_result(e: ToolResultEvent) -> bool:
    return e.tool_name == "find"

def is_ls_tool_result(e: ToolResultEvent) -> bool:
    return e.tool_name == "ls"

def is_tool_call_event_type(tool_name: str, event: ToolCallEvent) -> bool:
    return event.tool_name == tool_name

# ---------------------------------------------------------------------------
# All extension events union
# ---------------------------------------------------------------------------

ExtensionEvent = Union[
    ResourcesDiscoverEvent,
    SessionStartEvent,
    SessionBeforeSwitchEvent,
    SessionBeforeForkEvent,
    SessionBeforeCompactEvent,
    SessionCompactEvent,
    SessionShutdownEvent,
    SessionBeforeTreeEvent,
    SessionTreeEvent,
    ContextEvent,
    BeforeProviderRequestEvent,
    AfterProviderResponseEvent,
    BeforeAgentStartEvent,
    AgentStartEvent,
    AgentEndEvent,
    TurnStartEvent,
    TurnEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    MessageEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    ToolExecutionEndEvent,
    ModelSelectEvent,
    ThinkingLevelSelectEvent,
    UserBashEvent,
    InputEvent,
    ToolCallEvent,
    ToolResultEvent,
]

# ---------------------------------------------------------------------------
# Event Results
# ---------------------------------------------------------------------------

@dataclass
class ContextEventResult:
    messages: Optional[list[BaseMessage]] = None


@dataclass
class ToolCallEventResult:
    block: Optional[bool] = None
    reason: Optional[str] = None


@dataclass
class UserBashEventResult:
    operations: Optional[BashOperations] = None
    result: Optional[BashResult] = None


@dataclass
class ToolResultEventResult:
    content: Optional[list[Union[TextContent, ImageContent]]] = None
    details: Optional[Any] = None
    is_error: Optional[bool] = None


@dataclass
class MessageEndEventResult:
    message: Optional[BaseMessage] = None


@dataclass
class BeforeAgentStartEventResult:
    message: Optional[dict] = None
    system_prompt: Optional[str] = None


@dataclass
class SessionBeforeSwitchResult:
    cancel: Optional[bool] = None


@dataclass
class SessionBeforeForkResult:
    cancel: Optional[bool] = None
    skip_conversation_restore: Optional[bool] = None


@dataclass
class SessionBeforeCompactResult:
    cancel: Optional[bool] = None
    compaction: Optional[CompactionResult] = None


@dataclass
class SessionBeforeTreeResult:
    cancel: Optional[bool] = None
    summary: Optional[dict] = None
    custom_instructions: Optional[str] = None
    replace_instructions: Optional[bool] = None
    label: Optional[str] = None


# ---------------------------------------------------------------------------
# Extension Handler type
# ---------------------------------------------------------------------------

# Handler: (event, ctx) -> result | None (sync or async)
ExtensionHandler = Callable[..., Any]

# ---------------------------------------------------------------------------
# Extension API
# ---------------------------------------------------------------------------

class ExtensionAPI(ABC):
    """API passed to extension factory functions."""

    @abstractmethod
    def on(self, event: str, handler: ExtensionHandler) -> None: ...

    @abstractmethod
    def register_tool(self, tool: ToolDefinition) -> None: ...

    @abstractmethod
    def register_command(self, name: str, options: dict) -> None: ...

    @abstractmethod
    def register_flag(self, name: str, options: dict) -> None: ...

    @abstractmethod
    def get_flag(self, name: str) -> Optional[Union[bool, str]]: ...

    @abstractmethod
    def send_message(self, message: dict, options: Optional[dict] = None) -> None: ...

    @abstractmethod
    def send_user_message(self, content: Union[str, list], options: Optional[dict] = None) -> None: ...

    @abstractmethod
    def append_entry(self, custom_type: str, data: Any = None) -> None: ...

    @abstractmethod
    def set_session_name(self, name: str) -> None: ...

    @abstractmethod
    def get_session_name(self) -> Optional[str]: ...

    @abstractmethod
    def set_label(self, entry_id: str, label: Optional[str]) -> None: ...

    @abstractmethod
    async def exec(self, command: str, args: list[str], options: Optional[dict] = None) -> Any: ...

    @abstractmethod
    def get_active_tools(self) -> list[str]: ...

    @abstractmethod
    def get_all_tools(self) -> list[ToolInfo]: ...

    @abstractmethod
    def set_active_tools(self, tool_names: list[str]) -> None: ...

    @abstractmethod
    def get_commands(self) -> list[SlashCommandInfo]: ...

    @abstractmethod
    async def set_model(self, model: Model) -> bool: ...

    @abstractmethod
    def get_thinking_level(self) -> ThinkingLevel: ...

    @abstractmethod
    def set_thinking_level(self, level: ThinkingLevel) -> None: ...

    @abstractmethod
    def register_provider(self, name: str, options: dict) -> None: ...

    @abstractmethod
    def unregister_provider(self, name: str) -> None: ...


# ---------------------------------------------------------------------------
# Provider Registration Types
# ---------------------------------------------------------------------------

@dataclass
class ProviderModelConfig:
    id: str
    name: str
    reasoning: bool
    input: list[Literal["text", "image"]]
    cost: dict  # {input, output, cache_read, cache_write}
    context_window: int
    max_tokens: int
    api: Optional[Any] = None
    base_url: Optional[str] = None
    headers: Optional[dict[str, str]] = None
    compat: Optional[Any] = None
    thinking_level_map: Optional[Any] = None


@dataclass
class ProviderConfig:
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    api: Optional[Any] = None
    stream_simple: Optional[Callable] = None
    headers: Optional[dict[str, str]] = None
    auth_header: Optional[bool] = None
    models: Optional[list[ProviderModelConfig]] = None
    oauth: Optional[dict] = None


ExtensionFactory = Callable[["ExtensionAPI"], Any]

# ---------------------------------------------------------------------------
# Loaded Extension Types
# ---------------------------------------------------------------------------

@dataclass
class RegisteredTool:
    definition: ToolDefinition
    source_info: Optional[SourceInfo] = None


@dataclass
class ExtensionFlag:
    name: str
    type: Literal["boolean", "string"]
    extension_path: str
    description: Optional[str] = None
    default: Optional[Union[bool, str]] = None


# Handler type aliases
SendMessageHandler = Callable[..., None]
SendUserMessageHandler = Callable[..., None]
AppendEntryHandler = Callable[..., None]
SetSessionNameHandler = Callable[[str], None]
GetSessionNameHandler = Callable[[], Optional[str]]
GetActiveToolsHandler = Callable[[], list[str]]
GetAllToolsHandler = Callable[[], list[ToolInfo]]
GetCommandsHandler = Callable[[], list[Any]]
SetActiveToolsHandler = Callable[[list[str]], None]
RefreshToolsHandler = Callable[[], None]
SetModelHandler = Callable[[Any], Any]
GetThinkingLevelHandler = Callable[[], "ThinkingLevel"]
SetThinkingLevelHandler = Callable[["ThinkingLevel"], None]
SetLabelHandler = Callable[[str, Optional[str]], None]


class ExtensionRuntimeState:
    """Shared state created by loader. Actions are throwing stubs until runner binds."""

    def __init__(self) -> None:
        self.flag_values: dict[str, Union[bool, str]] = {}
        self.pending_provider_registrations: list[dict] = []
        self._stale_message: Optional[str] = None

        def _throwing_register(name: str, config: Any, extension_path: Optional[str] = None) -> None:
            self.pending_provider_registrations.append(
                {"name": name, "config": config, "extension_path": extension_path}
            )

        def _throwing_unregister(name: str, extension_path: Optional[str] = None) -> None:
            self.pending_provider_registrations = [
                r for r in self.pending_provider_registrations if r["name"] != name
            ]

        self.register_provider: Callable = _throwing_register
        self.unregister_provider: Callable = _throwing_unregister

    def assert_active(self) -> None:
        if self._stale_message:
            raise RuntimeError(self._stale_message)

    def invalidate(self, message: Optional[str] = None) -> None:
        self._stale_message = message or (
            "This extension instance is stale after session replacement or reload."
        )


@dataclass
class ExtensionActions:
    send_message: SendMessageHandler = field(default_factory=lambda: lambda *a, **kw: None)
    send_user_message: SendUserMessageHandler = field(default_factory=lambda: lambda *a, **kw: None)
    append_entry: AppendEntryHandler = field(default_factory=lambda: lambda *a, **kw: None)
    set_session_name: SetSessionNameHandler = field(default_factory=lambda: lambda _: None)
    get_session_name: GetSessionNameHandler = field(default_factory=lambda: lambda: None)
    set_label: SetLabelHandler = field(default_factory=lambda: lambda *a: None)
    get_active_tools: GetActiveToolsHandler = field(default_factory=lambda: lambda: [])
    get_all_tools: GetAllToolsHandler = field(default_factory=lambda: lambda: [])
    set_active_tools: SetActiveToolsHandler = field(default_factory=lambda: lambda _: None)
    refresh_tools: RefreshToolsHandler = field(default_factory=lambda: lambda: None)
    get_commands: GetCommandsHandler = field(default_factory=lambda: lambda: [])
    set_model: SetModelHandler = field(default_factory=lambda: lambda _: None)
    get_thinking_level: GetThinkingLevelHandler = field(default_factory=lambda: lambda: None)
    set_thinking_level: SetThinkingLevelHandler = field(default_factory=lambda: lambda _: None)


@dataclass
class ExtensionContextActions:
    get_model: Callable[[], Optional[Any]] = field(default_factory=lambda: lambda: None)
    is_idle: Callable[[], bool] = field(default_factory=lambda: lambda: True)
    get_signal: Callable[[], Optional[AbortSignal]] = field(default_factory=lambda: lambda: None)
    abort: Callable[[], None] = field(default_factory=lambda: lambda: None)
    has_pending_messages: Callable[[], bool] = field(default_factory=lambda: lambda: False)
    shutdown: Callable[[], None] = field(default_factory=lambda: lambda: None)
    get_context_usage: Callable[[], Optional["ContextUsage"]] = field(default_factory=lambda: lambda: None)
    compact: Callable[..., None] = field(default_factory=lambda: lambda *a, **kw: None)
    get_system_prompt: Callable[[], str] = field(default_factory=lambda: lambda: "")


@dataclass
class ExtensionCommandContextActions:
    wait_for_idle: Callable[[], Any] = field(default_factory=lambda: lambda: None)
    new_session: Callable[..., Any] = field(default_factory=lambda: lambda *a, **kw: {"cancelled": False})
    fork: Callable[..., Any] = field(default_factory=lambda: lambda *a, **kw: {"cancelled": False})
    navigate_tree: Callable[..., Any] = field(default_factory=lambda: lambda *a, **kw: {"cancelled": False})
    switch_session: Callable[..., Any] = field(default_factory=lambda: lambda *a, **kw: {"cancelled": False})
    reload: Callable[[], Any] = field(default_factory=lambda: lambda: None)


class ExtensionRuntime(ExtensionRuntimeState, ExtensionActions):
    """Full runtime: state + action bindings. Created by loader, completed by runner."""

    def __init__(self) -> None:
        ExtensionRuntimeState.__init__(self)
        ExtensionActions.__init__(self)


@dataclass
class Extension:
    """Loaded extension with all registered items."""
    path: str
    resolved_path: str
    source_info: Optional[SourceInfo]
    handlers: dict[str, list[Callable]] = field(default_factory=dict)
    tools: dict[str, RegisteredTool] = field(default_factory=dict)
    commands: dict[str, RegisteredCommand] = field(default_factory=dict)
    flags: dict[str, ExtensionFlag] = field(default_factory=dict)


@dataclass
class LoadExtensionsResult:
    extensions: list[Extension]
    errors: list[dict]
    runtime: ExtensionRuntime


@dataclass
class ExtensionError:
    extension_path: str
    event: str
    error: str
    stack: Optional[str] = None

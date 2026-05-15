from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, TYPE_CHECKING

from pydantic import BaseModel

from program.tool.types import ToolExecutionMode, ToolResult
from program.skill.types import SourceInfo, ResourceDiagnostic
from program.bus.service import EventBus

if TYPE_CHECKING:
    from program.session.manager import SessionManager
    from program.session.types import SessionEntry, CompactionEntry, BranchEntry
    from program.compaction.types import CompactionPreparation, CompactionResult
    from program.message.types import AgentMessage
    from program.llm.types import ThinkingLevel
    from program.llm.model.types import Model


# ============================================================================
# Extension context (implemented by the agent runtime)
# ============================================================================

class ContextUsage(BaseModel):
    tokens: int | None
    context_window: int
    percent: float | None


class CompactOptions(BaseModel):
    custom_instructions: str | None = None
    on_complete: Any | None = None   # Callable[[CompactionResult], None]
    on_error: Any | None = None      # Callable[[Exception], None]


class ExtensionContext(ABC):
    """Read-only context passed to every event handler."""

    @property
    @abstractmethod
    def cwd(self) -> Path: ...

    @property
    @abstractmethod
    def session_manager(self) -> Any: ...   # ReadonlySessionManager

    @property
    @abstractmethod
    def model(self) -> Any | None: ...      # Model | None

    @abstractmethod
    def is_idle(self) -> bool: ...

    @abstractmethod
    def has_pending_messages(self) -> bool: ...

    @abstractmethod
    def abort(self) -> None: ...

    @abstractmethod
    def shutdown(self) -> None: ...

    @abstractmethod
    def get_context_usage(self) -> ContextUsage | None: ...

    @abstractmethod
    def get_system_prompt(self) -> str: ...

    @abstractmethod
    def compact(self, options: CompactOptions | None = None) -> None: ...


# ============================================================================
# Tool definition (used by extension authors)
# ============================================================================

@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: type[BaseModel]      # Pydantic model — schema sent to LLM
    execute: Callable[..., Awaitable[ToolResult]]
    label: str = ""
    prompt_snippet: str | None = None
    prompt_guidelines: list[str] = field(default_factory=list)
    execution_mode: ToolExecutionMode = ToolExecutionMode.Sequential


@dataclass
class RegisteredTool:
    definition: ToolDefinition
    source_info: SourceInfo


# ============================================================================
# Command registration
# ============================================================================

@dataclass
class RegisteredCommand:
    name: str
    source_info: SourceInfo
    description: str | None = None
    handler: Callable[..., Awaitable[None]] = field(default=lambda *a: None)


# ============================================================================
# Events — Session
# ============================================================================

@dataclass
class SessionStartEvent:
    type: Literal['session_start'] = field(default='session_start', init=False)
    reason: Literal['startup', 'reload', 'new', 'resume', 'fork'] = 'startup'
    previous_session_file: str | None = None


@dataclass
class SessionBeforeSwitchEvent:
    type: Literal['session_before_switch'] = field(default='session_before_switch', init=False)
    reason: Literal['new', 'resume'] = 'new'
    target_session_file: str | None = None


@dataclass
class SessionBeforeForkEvent:
    type: Literal['session_before_fork'] = field(default='session_before_fork', init=False)
    entry_id: str = ''
    position: Literal['before', 'at'] = 'at'


@dataclass
class SessionBeforeCompactEvent:
    type: Literal['session_before_compact'] = field(default='session_before_compact', init=False)
    preparation: Any = None          # CompactionPreparation
    branch_entries: list[Any] = field(default_factory=list)   # list[SessionEntry]
    custom_instructions: str | None = None


@dataclass
class SessionCompactEvent:
    type: Literal['session_compact'] = field(default='session_compact', init=False)
    compaction_entry: Any = None     # CompactionEntry
    from_extension: bool = False


@dataclass
class SessionShutdownEvent:
    type: Literal['session_shutdown'] = field(default='session_shutdown', init=False)
    reason: Literal['quit', 'reload', 'new', 'resume', 'fork'] = 'quit'
    target_session_file: str | None = None


@dataclass
class TreePreparation:
    target_id: str
    old_leaf_id: str | None
    common_ancestor_id: str | None
    entries_to_summarize: list[Any]      # list[SessionEntry]
    user_wants_summary: bool = False
    custom_instructions: str | None = None
    replace_instructions: bool = False
    label: str | None = None


@dataclass
class SessionBeforeTreeEvent:
    type: Literal['session_before_tree'] = field(default='session_before_tree', init=False)
    preparation: TreePreparation = field(default_factory=lambda: TreePreparation('', None, None, []))


@dataclass
class SessionTreeEvent:
    type: Literal['session_tree'] = field(default='session_tree', init=False)
    new_leaf_id: str | None = None
    old_leaf_id: str | None = None
    summary_entry: Any | None = None     # BranchEntry | None
    from_extension: bool = False


# ============================================================================
# Events — Agent
# ============================================================================

@dataclass
class ContextEvent:
    type: Literal['context'] = field(default='context', init=False)
    messages: list[Any] = field(default_factory=list)    # list[AgentMessage]


@dataclass
class BeforeAgentStartEvent:
    type: Literal['before_agent_start'] = field(default='before_agent_start', init=False)
    prompt: str = ''
    system_prompt: str = ''


@dataclass
class AgentStartEvent:
    type: Literal['agent_start'] = field(default='agent_start', init=False)


@dataclass
class AgentEndEvent:
    type: Literal['agent_end'] = field(default='agent_end', init=False)
    messages: list[Any] = field(default_factory=list)    # list[AgentMessage]


@dataclass
class TurnStartEvent:
    type: Literal['turn_start'] = field(default='turn_start', init=False)
    turn_index: int = 0
    timestamp: float = 0.0


@dataclass
class TurnEndEvent:
    type: Literal['turn_end'] = field(default='turn_end', init=False)
    turn_index: int = 0
    message: Any = None                  # AgentMessage


@dataclass
class MessageStartEvent:
    type: Literal['message_start'] = field(default='message_start', init=False)
    message: Any = None


@dataclass
class MessageUpdateEvent:
    type: Literal['message_update'] = field(default='message_update', init=False)
    message: Any = None


@dataclass
class MessageEndEvent:
    type: Literal['message_end'] = field(default='message_end', init=False)
    message: Any = None


# ============================================================================
# Events — Tool execution
# ============================================================================

@dataclass
class ToolExecutionStartEvent:
    type: Literal['tool_execution_start'] = field(default='tool_execution_start', init=False)
    tool_call_id: str = ''
    tool_name: str = ''
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolExecutionUpdateEvent:
    type: Literal['tool_execution_update'] = field(default='tool_execution_update', init=False)
    tool_call_id: str = ''
    tool_name: str = ''
    args: dict[str, Any] = field(default_factory=dict)
    partial_result: Any = None


@dataclass
class ToolExecutionEndEvent:
    type: Literal['tool_execution_end'] = field(default='tool_execution_end', init=False)
    tool_call_id: str = ''
    tool_name: str = ''
    result: Any = None
    is_error: bool = False


# ============================================================================
# Events — Tool call / result (before/after)
# ============================================================================

@dataclass
class ToolCallEvent:
    type: Literal['tool_call'] = field(default='tool_call', init=False)
    tool_call_id: str = ''
    tool_name: str = ''
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResultEvent:
    type: Literal['tool_result'] = field(default='tool_result', init=False)
    tool_call_id: str = ''
    tool_name: str = ''
    input: dict[str, Any] = field(default_factory=dict)
    content: str = ''
    is_error: bool = False


# ============================================================================
# Events — Model
# ============================================================================

@dataclass
class ModelSelectEvent:
    type: Literal['model_select'] = field(default='model_select', init=False)
    model: Any = None
    previous_model: Any | None = None
    source: Literal['set', 'cycle', 'restore'] = 'set'


@dataclass
class ThinkingLevelSelectEvent:
    type: Literal['thinking_level_select'] = field(default='thinking_level_select', init=False)
    level: Any = None           # ThinkingLevel
    previous_level: Any = None  # ThinkingLevel


# ============================================================================
# Events — Input / Bash
# ============================================================================

@dataclass
class InputEvent:
    type: Literal['input'] = field(default='input', init=False)
    text: str = ''
    source: Literal['interactive', 'rpc', 'extension'] = 'interactive'


@dataclass
class UserBashEvent:
    type: Literal['user_bash'] = field(default='user_bash', init=False)
    command: str = ''
    exclude_from_context: bool = False
    cwd: str = ''


@dataclass
class ResourcesDiscoverEvent:
    type: Literal['resources_discover'] = field(default='resources_discover', init=False)
    cwd: str = ''
    reason: Literal['startup', 'reload'] = 'startup'


# Union of all events
ExtensionEvent = (
    ResourcesDiscoverEvent
    | SessionStartEvent
    | SessionBeforeSwitchEvent
    | SessionBeforeForkEvent
    | SessionBeforeCompactEvent
    | SessionCompactEvent
    | SessionShutdownEvent
    | SessionBeforeTreeEvent
    | SessionTreeEvent
    | ContextEvent
    | BeforeAgentStartEvent
    | AgentStartEvent
    | AgentEndEvent
    | TurnStartEvent
    | TurnEndEvent
    | MessageStartEvent
    | MessageUpdateEvent
    | MessageEndEvent
    | ToolExecutionStartEvent
    | ToolExecutionUpdateEvent
    | ToolExecutionEndEvent
    | ToolCallEvent
    | ToolResultEvent
    | ModelSelectEvent
    | ThinkingLevelSelectEvent
    | InputEvent
    | UserBashEvent
)

# ============================================================================
# Event results
# ============================================================================

@dataclass
class ResourcesDiscoverResult:
    skill_paths: list[str] = field(default_factory=list)

@dataclass
class ContextEventResult:
    messages: list[Any] | None = None   # list[AgentMessage] | None

@dataclass
class ToolCallEventResult:
    block: bool = False
    reason: str | None = None

@dataclass
class ToolResultEventResult:
    content: str | None = None
    is_error: bool | None = None

@dataclass
class MessageEndEventResult:
    message: Any | None = None          # AgentMessage | None

@dataclass
class BeforeAgentStartEventResult:
    system_prompt: str | None = None

@dataclass
class SessionBeforeSwitchResult:
    cancel: bool = False

@dataclass
class SessionBeforeForkResult:
    cancel: bool = False

@dataclass
class SessionBeforeCompactResult:
    cancel: bool = False
    compaction: Any | None = None       # CompactionResult | None

@dataclass
class SessionBeforeTreeResult:
    cancel: bool = False
    summary: dict[str, Any] | None = None
    custom_instructions: str | None = None
    replace_instructions: bool | None = None
    label: str | None = None

@dataclass
class InputEventResult:
    action: Literal['continue', 'transform', 'handled'] = 'continue'
    text: str | None = None

# ============================================================================
# Handler and factory types
# ============================================================================

EventHandler = Callable[[Any, ExtensionContext], Awaitable[Any] | Any]
ExtensionFactory = Callable[['ExtensionAPI'], Awaitable[None] | None]

# ============================================================================
# Loaded extension state
# ============================================================================

@dataclass
class ExtensionError:
    extension_path: str
    event: str
    error: str
    stack: str | None = None


@dataclass
class Extension:
    path: str
    source_info: SourceInfo
    handlers: dict[str, list[EventHandler]] = field(default_factory=dict)
    tools: dict[str, RegisteredTool] = field(default_factory=dict)
    commands: dict[str, RegisteredCommand] = field(default_factory=dict)


@dataclass
class LoadExtensionsResult:
    extensions: list[Extension] = field(default_factory=list)
    errors: list[ExtensionError] = field(default_factory=list)


# ============================================================================
# Extension API (registration interface given to extension factory)
# ============================================================================

class ExtensionAPI:
    """
    Passed to an extension's factory function.
    The extension calls on(), register_tool(), register_command() to hook in.
    """

    def __init__(self, extension: Extension, bus: EventBus) -> None:
        self._extension = extension
        self.events = bus        # shared pub/sub for extension-to-extension communication

    # -- Event subscription --------------------------------------------------

    def on(self, event: str, handler: EventHandler) -> None:
        self._extension.handlers.setdefault(event, []).append(handler)

    # -- Tool registration ---------------------------------------------------

    def register_tool(self, tool: ToolDefinition) -> None:
        source_info = SourceInfo(
            path=self._extension.path,
            source='extension',
        )
        self._extension.tools[tool.name] = RegisteredTool(
            definition=tool,
            source_info=source_info,
        )

    # -- Command registration ------------------------------------------------

    def register_command(
        self,
        name: str,
        handler: Callable[..., Awaitable[None]],
        description: str | None = None,
    ) -> None:
        source_info = SourceInfo(
            path=self._extension.path,
            source='extension',
        )
        self._extension.commands[name] = RegisteredCommand(
            name=name,
            source_info=source_info,
            description=description,
            handler=handler,
        )

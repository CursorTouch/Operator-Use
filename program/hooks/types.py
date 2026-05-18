from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from program.message.types import BaseMessage, ToolCallContent, ToolResultContent


# ============================================================================
# Session lifecycle
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
    preparation: Any = None
    branch_entries: list[Any] = field(default_factory=list)
    custom_instructions: str | None = None


@dataclass
class SessionCompactEvent:
    type: Literal['session_compact'] = field(default='session_compact', init=False)
    compaction_entry: Any = None
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
    entries_to_summarize: list[Any]
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
    summary_entry: Any | None = None
    from_extension: bool = False


# ============================================================================
# Agent lifecycle
# ============================================================================

@dataclass
class ContextEvent:
    type: Literal['context'] = field(default='context', init=False)
    messages: list[Any] = field(default_factory=list)


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
    messages: list[Any] = field(default_factory=list)


@dataclass
class AgentErrorEvent:
    type: Literal['agent_error'] = field(default='agent_error', init=False)
    error: str = ''


# ============================================================================
# Turn lifecycle
# ============================================================================

@dataclass
class TurnStartEvent:
    type: Literal['turn_start'] = field(default='turn_start', init=False)
    turn_index: int = 0
    timestamp: float = 0.0


@dataclass
class TurnEndEvent:
    type: Literal['turn_end'] = field(default='turn_end', init=False)
    turn_index: int = 0
    message: Any = None
    tool_results: list[Any] = field(default_factory=list)


# ============================================================================
# Message lifecycle
# ============================================================================

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
# Tool execution
# ============================================================================

@dataclass
class ToolExecutionStartEvent:
    type: Literal['tool_execution_start'] = field(default='tool_execution_start', init=False)
    tool_call: Any = None       # ToolCallContent


@dataclass
class ToolExecutionUpdateEvent:
    type: Literal['tool_execution_update'] = field(default='tool_execution_update', init=False)
    partial_tool_result: Any = None     # ToolResultContent


@dataclass
class ToolExecutionEndEvent:
    type: Literal['tool_execution_end'] = field(default='tool_execution_end', init=False)
    tool_result: Any = None     # ToolResultContent


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
# Model / thinking
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
    level: Any = None
    previous_level: Any = None


# ============================================================================
# Input / resources / misc
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


@dataclass
class SavePointEvent:
    """Fires after session writes are flushed — harness is idle and consistent."""
    type: Literal['save_point'] = field(default='save_point', init=False)


@dataclass
class SettledEvent:
    """Fires when the agent finishes a prompt() call with no more queued turns."""
    type: Literal['settled'] = field(default='settled', init=False)


# ============================================================================
# Union of all hook events
# ============================================================================

HookEvent = (
    SessionStartEvent
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
    | AgentErrorEvent
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
    | ResourcesDiscoverEvent
    | SavePointEvent
    | SettledEvent
)


# ============================================================================
# Hook result types (returned by handlers to influence behaviour)
# ============================================================================

@dataclass
class ResourcesDiscoverResult:
    skill_paths: list[str] = field(default_factory=list)


@dataclass
class ContextEventResult:
    messages: list[Any] | None = None


@dataclass
class ToolCallEventResult:
    block: bool = False
    reason: str | None = None


@dataclass
class ToolResultEventResult:
    content: str | None = None
    is_error: bool | None = None
    terminate: bool = False


@dataclass
class MessageEndEventResult:
    message: Any | None = None


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
    compaction: Any | None = None


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
# Gateway events (transport layer — channel and message lifecycle)
# ============================================================================

@dataclass
class ChannelConnectEvent:
    """Fired when a channel is registered with the gateway."""
    type: Literal['channel:connect'] = field(default='channel:connect', init=False)
    channel_id: str = ''


@dataclass
class ChannelDisconnectEvent:
    """Fired when a channel is unregistered from the gateway."""
    type: Literal['channel:disconnect'] = field(default='channel:disconnect', init=False)
    channel_id: str = ''


@dataclass
class MessageReceiveEvent:
    """
    Fired when a message arrives from a channel, before the agent processes it.
    Handlers can return MessageReceiveResult to reject or transform the message.
    """
    type: Literal['message:receive'] = field(default='message:receive', init=False)
    channel_id: str = ''
    chat_id: str = ''
    user_id: str = ''
    text: str = ''


@dataclass
class MessageSendEvent:
    """Fired after a full response has been delivered to a channel."""
    type: Literal['message:send'] = field(default='message:send', init=False)
    channel_id: str = ''
    chat_id: str = ''
    input_text: str = ''
    response_text: str = ''


@dataclass
class GatewayStartupEvent:
    """Fired after all enabled channels have been registered and started."""
    type: Literal['gateway:startup'] = field(default='gateway:startup', init=False)
    channel_ids: list[str] = field(default_factory=list)


@dataclass
class GatewayStopEvent:
    """Fired when the gateway is shutting down, before channel tasks are cancelled."""
    type: Literal['gateway:stop'] = field(default='gateway:stop', init=False)
    channel_ids: list[str] = field(default_factory=list)


@dataclass
class GatewayErrorEvent:
    """Fired when an error occurs while processing a message."""
    type: Literal['gateway:error'] = field(default='gateway:error', init=False)
    channel_id: str = ''
    error: str = ''


# ============================================================================
# Gateway result types
# ============================================================================

@dataclass
class MessageReceiveResult:
    """
    Returned by message:receive handlers to control what happens next.

    action='continue'  — pass the message through unchanged (default).
    action='transform' — replace the message text with `text`.
    action='reject'    — drop the message (optional `reason` sent to the channel).
    """
    action: Literal['continue', 'transform', 'reject'] = 'continue'
    text: str | None = None
    reason: str | None = None


@dataclass
class ChannelConnectResult:
    """
    Returned by channel:connect handlers.
    allow=False causes the channel to be unregistered immediately.
    """
    allow: bool = True
    reason: str | None = None

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from pydantic import BaseModel

from program.tool.types import ToolExecutionMode, ToolResult
from program.skill.types import SourceInfo, ResourceDiagnostic
from program.bus.service import EventBus

# All hook event and result types live in program.hooks — re-exported here for backward compat
from program.hooks.types import (
    SessionStartEvent, SessionBeforeSwitchEvent, SessionBeforeForkEvent,
    SessionBeforeCompactEvent, SessionCompactEvent, SessionShutdownEvent,
    TreePreparation, SessionBeforeTreeEvent, SessionTreeEvent,
    ContextEvent, BeforeAgentStartEvent, AgentStartEvent, AgentEndEvent, AgentErrorEvent,
    TurnStartEvent, TurnEndEvent,
    MessageStartEvent, MessageUpdateEvent, MessageEndEvent,
    ToolExecutionStartEvent, ToolExecutionUpdateEvent, ToolExecutionEndEvent,
    ToolCallEvent, ToolResultEvent,
    ModelSelectEvent, ThinkingLevelSelectEvent,
    InputEvent, UserBashEvent, ResourcesDiscoverEvent, SavePointEvent, SettledEvent,
    HookEvent,
    ResourcesDiscoverResult, ContextEventResult, ToolCallEventResult,
    ToolResultEventResult, MessageEndEventResult, BeforeAgentStartEventResult,
    SessionBeforeSwitchResult, SessionBeforeForkResult, SessionBeforeCompactResult,
    SessionBeforeTreeResult, InputEventResult,
)

ExtensionEvent = HookEvent  # backward-compat alias

if TYPE_CHECKING:
    from program.session.manager import SessionManager
    from program.compaction.types import CompactionResult
    from program.message.types import AgentMessage
    from program.inference.types import ThinkingLevel
    from program.inference.model.types import Model


# ============================================================================
# Extension context (implemented by the agent runtime)
# ============================================================================

class ContextUsage(BaseModel):
    tokens: int | None
    context_window: int
    percent: float | None


class CompactOptions(BaseModel):
    custom_instructions: str | None = None
    on_complete: Any | None = None
    on_error: Any | None = None


class ExtensionContext(ABC):
    """Read-only context passed to every event handler."""

    @property
    @abstractmethod
    def cwd(self) -> Path: ...

    @property
    @abstractmethod
    def session_manager(self) -> Any: ...

    @property
    @abstractmethod
    def model(self) -> Any | None: ...

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
    parameters: type[BaseModel]
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
        self.events = bus

    def on(self, event: str, handler: EventHandler) -> None:
        self._extension.handlers.setdefault(event, []).append(handler)

    def register_tool(self, tool: ToolDefinition) -> None:
        source_info = SourceInfo(path=self._extension.path, source='extension')
        self._extension.tools[tool.name] = RegisteredTool(definition=tool, source_info=source_info)

    def register_command(
        self,
        name: str,
        handler: Callable[..., Awaitable[None]],
        description: str | None = None,
    ) -> None:
        source_info = SourceInfo(path=self._extension.path, source='extension')
        self._extension.commands[name] = RegisteredCommand(
            name=name,
            source_info=source_info,
            description=description,
            handler=handler,
        )

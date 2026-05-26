from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from pydantic import BaseModel

from program.tool.types import Tool, ToolContext, ToolKind, ToolInvocation, ToolExecutionMode, ToolResult, ToolExecutionUpdateCallback, AbortSignal
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
    """Context passed to every event handler and slash-command."""

    # ── Identity & state ──────────────────────────────────────────────────────

    @property
    @abstractmethod
    def cwd(self) -> Path: ...

    @property
    @abstractmethod
    def session_manager(self) -> Any: ...

    @property
    @abstractmethod
    def model(self) -> Any | None: ...

    @property
    @abstractmethod
    def model_registry(self) -> Any: ...

    @property
    @abstractmethod
    def signal(self) -> Any:
        """The engine's current abort signal (asyncio.Event)."""
        ...

    # ── Query ─────────────────────────────────────────────────────────────────

    @abstractmethod
    def is_idle(self) -> bool: ...

    @abstractmethod
    def has_pending_messages(self) -> bool: ...

    @abstractmethod
    def get_context_usage(self) -> ContextUsage | None: ...

    @abstractmethod
    def get_system_prompt(self) -> str: ...

    # ── Control ───────────────────────────────────────────────────────────────

    @abstractmethod
    def abort(self) -> None: ...

    @abstractmethod
    def shutdown(self) -> None: ...

    @abstractmethod
    def compact(self, options: CompactOptions | None = None) -> None: ...

    @abstractmethod
    async def reload(self) -> None:
        """Hot-reload extensions, skills, and context files."""
        ...

    @abstractmethod
    async def wait_for_idle(self) -> None:
        """Suspend until the engine finishes its current turn."""
        ...

    # ── Session lifecycle (delegates to Runtime) ──────────────────────────────

    @abstractmethod
    async def new_session(self) -> None:
        """Shut down the current session and start a fresh one."""
        ...

    @abstractmethod
    async def fork(self, entry_id: str) -> None:
        """Branch the session tree at entry_id and start a new leaf."""
        ...

    @abstractmethod
    async def switch_session(self, session_file: Path) -> None:
        """Shut down the current session and resume one from a file."""
        ...


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
    kind: ToolKind = ToolKind.Unknown


@dataclass
class RegisteredTool:
    definition: ToolDefinition
    source_info: SourceInfo


class ExtensionTool(Tool):
    """Adapts a ToolDefinition from an extension into a Tool the engine can execute."""

    def __init__(self, definition: ToolDefinition, ctx: ExtensionContext) -> None:
        super().__init__(
            name=definition.name,
            description=definition.description,
            schema=definition.parameters,
            kind=definition.kind,
            execution_mode=definition.execution_mode,
        )
        self._definition = definition
        self._ctx = ctx

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = self._definition.parameters.model_validate(invocation.params)
        return await self._definition.execute(params, invocation, self._ctx)


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
    config: dict = field(default_factory=dict)
    # Provider registrations collected during factory execution
    inference_providers: list[Any] = field(default_factory=list)  # APIProvider | OAuthProvider
    inference_apis: dict[str, Any] = field(default_factory=dict)  # LLM API classes keyed by name
    memory_providers: list[Any] = field(default_factory=list)     # MemoryProvider descriptors
    memory_apis: dict[str, Any] = field(default_factory=dict)     # BaseMemoryAPI classes keyed by name
    subagent_profiles: list[Any] = field(default_factory=list)    # SubagentProfile instances


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

    @property
    def config(self) -> dict:
        """Per-extension settings dict from extension_list in settings.json."""
        return self._extension.config

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

    def register_provider(self, provider: Any) -> None:
        """Register a custom inference provider (APIProvider or OAuthProvider)."""
        self._extension.inference_providers.append(provider)

    def register_llm_api(self, name: str, api: Any) -> None:
        """Register a custom LLM API class under the given name."""
        self._extension.inference_apis[name] = api

    def register_memory_provider(self, provider: Any) -> None:
        """Register a custom memory provider (MemoryProvider descriptor)."""
        self._extension.memory_providers.append(provider)

    def register_memory_api(self, name: str, api: Any) -> None:
        """Register a custom memory API class (BaseMemoryAPI subclass) under the given name."""
        self._extension.memory_apis[name] = api

    def register_subagent_profile(self, profile: Any) -> None:
        """Register a SubagentProfile so it is available to the subagent tool."""
        self._extension.subagent_profiles.append(profile)

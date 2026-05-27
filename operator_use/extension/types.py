from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from pydantic import BaseModel

from operator_use.tool.types import Tool, ToolContext, ToolKind, ToolInvocation, ToolExecutionMode, ToolResult, ToolExecutionUpdateCallback, AbortSignal
from operator_use.skill.types import SourceInfo, ResourceDiagnostic
from operator_use.bus.service import EventBus

if TYPE_CHECKING:
    from typing import Type
    from operator_use.inference.provider.types import APIProvider, OAuthProvider, ImageProvider, AudioProvider, VideoProvider
    from operator_use.inference.api.text.base import BaseLLMAPI
    from operator_use.inference.api.image.base import BaseImageAPI
    from operator_use.inference.api.audio.base import BaseAudioAPI
    from operator_use.inference.api.video.base import BaseVideoAPI
    from operator_use.memory.provider.types import MemoryProvider
    from operator_use.memory.api.base import BaseMemoryAPI
    from operator_use.subagent.profile import SubagentProfile

# All hook event and result types live in program.hooks — re-exported here for backward compat
from operator_use.hooks.types import (
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
    from operator_use.session.manager import SessionManager
    from operator_use.compaction.strategy.types import CompactionResult
    from operator_use.message.types import AgentMessage
    from operator_use.inference.types import ThinkingLevel
    from operator_use.inference.model.types import Model


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
    handler: Callable[..., Awaitable[None] | None] = field(default=lambda *a: None)


# ============================================================================
# Inference provider registration
# ============================================================================

@dataclass
class RegisteredInferenceProvider:
    provider: APIProvider | OAuthProvider
    source_info: SourceInfo


@dataclass
class RegisteredImageProvider:
    provider: ImageProvider
    source_info: SourceInfo


@dataclass
class RegisteredAudioProvider:
    provider: AudioProvider
    source_info: SourceInfo


@dataclass
class RegisteredVideoProvider:
    provider: VideoProvider
    source_info: SourceInfo


@dataclass
class RegisteredTextAPI:
    name: str
    api: Type[BaseLLMAPI]
    source_info: SourceInfo


@dataclass
class RegisteredImageAPI:
    name: str
    api: Type[BaseImageAPI]
    source_info: SourceInfo


@dataclass
class RegisteredAudioAPI:
    name: str
    api: Type[BaseAudioAPI]
    source_info: SourceInfo


@dataclass
class RegisteredVideoAPI:
    name: str
    api: Type[BaseVideoAPI]
    source_info: SourceInfo


# ============================================================================
# Memory provider registration
# ============================================================================

@dataclass
class RegisteredMemoryProvider:
    provider: MemoryProvider
    source_info: SourceInfo


@dataclass
class RegisteredMemoryAPI:
    name: str
    api: Type[BaseMemoryAPI]
    source_info: SourceInfo


# ============================================================================
# Subagent profile registration
# ============================================================================

@dataclass
class RegisteredSubagentProfile:
    profile: SubagentProfile
    source_info: SourceInfo


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
    inference_providers: list[RegisteredInferenceProvider] = field(default_factory=list)
    image_providers: list[RegisteredImageProvider] = field(default_factory=list)
    audio_providers: list[RegisteredAudioProvider] = field(default_factory=list)
    video_providers: list[RegisteredVideoProvider] = field(default_factory=list)
    inference_apis: dict[str, RegisteredTextAPI] = field(default_factory=dict)
    image_apis: dict[str, RegisteredImageAPI] = field(default_factory=dict)
    audio_apis: dict[str, RegisteredAudioAPI] = field(default_factory=dict)
    video_apis: dict[str, RegisteredVideoAPI] = field(default_factory=dict)
    memory_providers: list[RegisteredMemoryProvider] = field(default_factory=list)
    memory_apis: dict[str, RegisteredMemoryAPI] = field(default_factory=dict)
    subagent_profiles: list[RegisteredSubagentProfile] = field(default_factory=list)


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

    def register_provider(self, provider: APIProvider | OAuthProvider) -> None:
        """Register a custom text inference provider (APIProvider or OAuthProvider)."""
        source_info = SourceInfo(path=self._extension.path, source='extension')
        self._extension.inference_providers.append(
            RegisteredInferenceProvider(provider=provider, source_info=source_info)
        )

    def register_image_provider(self, provider: ImageProvider) -> None:
        """Register a custom image generation provider."""
        source_info = SourceInfo(path=self._extension.path, source='extension')
        self._extension.image_providers.append(
            RegisteredImageProvider(provider=provider, source_info=source_info)
        )

    def register_audio_provider(self, provider: AudioProvider) -> None:
        """Register a custom audio (TTS/STT) provider."""
        source_info = SourceInfo(path=self._extension.path, source='extension')
        self._extension.audio_providers.append(
            RegisteredAudioProvider(provider=provider, source_info=source_info)
        )

    def register_video_provider(self, provider: VideoProvider) -> None:
        """Register a custom video generation provider."""
        source_info = SourceInfo(path=self._extension.path, source='extension')
        self._extension.video_providers.append(
            RegisteredVideoProvider(provider=provider, source_info=source_info)
        )

    def register_text_api(self, name: str, api: Type[BaseLLMAPI]) -> None:
        """Register a custom text LLM API class under the given name."""
        source_info = SourceInfo(path=self._extension.path, source='extension')
        self._extension.inference_apis[name] = RegisteredTextAPI(
            name=name, api=api, source_info=source_info
        )

    def register_image_api(self, name: str, api: Type[BaseImageAPI]) -> None:
        """Register a custom image generation API class under the given name."""
        source_info = SourceInfo(path=self._extension.path, source='extension')
        self._extension.image_apis[name] = RegisteredImageAPI(
            name=name, api=api, source_info=source_info
        )

    def register_audio_api(self, name: str, api: Type[BaseAudioAPI]) -> None:
        """Register a custom audio (TTS/STT) API class under the given name."""
        source_info = SourceInfo(path=self._extension.path, source='extension')
        self._extension.audio_apis[name] = RegisteredAudioAPI(
            name=name, api=api, source_info=source_info
        )

    def register_video_api(self, name: str, api: Type[BaseVideoAPI]) -> None:
        """Register a custom video generation API class under the given name."""
        source_info = SourceInfo(path=self._extension.path, source='extension')
        self._extension.video_apis[name] = RegisteredVideoAPI(
            name=name, api=api, source_info=source_info
        )

    def register_memory_provider(self, provider: MemoryProvider) -> None:
        """Register a custom memory provider (MemoryProvider descriptor)."""
        source_info = SourceInfo(path=self._extension.path, source='extension')
        self._extension.memory_providers.append(
            RegisteredMemoryProvider(provider=provider, source_info=source_info)
        )

    def register_memory_api(self, name: str, api: Type[BaseMemoryAPI]) -> None:
        """Register a custom memory API class (BaseMemoryAPI subclass) under the given name."""
        source_info = SourceInfo(path=self._extension.path, source='extension')
        self._extension.memory_apis[name] = RegisteredMemoryAPI(
            name=name, api=api, source_info=source_info
        )

    def register_subagent_profile(self, profile: SubagentProfile) -> None:
        """Register a SubagentProfile so it is available to the subagent tool."""
        source_info = SourceInfo(path=self._extension.path, source='extension')
        self._extension.subagent_profiles.append(
            RegisteredSubagentProfile(profile=profile, source_info=source_info)
        )

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from os.path import basename, dirname
from pathlib import Path
from typing import Any, Callable, Optional, TYPE_CHECKING

from program.agent.loop import Agent
from program.agent.types import (
    AgentEvent, AgentStartEvent, AgentEndEvent,
    TurnStartEvent, TurnEndEvent,
    MessageStartEvent, MessageUpdateEvent, MessageEndEvent,
    ToolExecutionStartEvent, ToolExecutionUpdateEvent, ToolExecutionEndEvent,
    AgentErrorEvent, AgentState, Options as AgentOptions,
    FollowupQueue, SteeringQueue, FollowupMode, SteeringMode,
)
from program.compaction import Compact, BranchCompact
from program.compaction.types import CompactionResult, DEFAULT_COMPACTION_SETTINGS
from program.compaction.utils import get_last_assistant_usage, calculate_context_tokens, should_compact
from program.extensions.runtime import ExtensionRunner, emit_session_shutdown_event, NoOpUIContext
from program.extensions.types import (
    ExtensionUIContext, ExtensionCommandContextActions,
    LoadExtensionsResult,
)
from program.extensions.runtime import ExtensionErrorListener
from program.llm.model.registry import ModelRegistry
from program.llm.model.types import Model
from program.llm.types import ThinkingLevel
from program.message.types import (
    AssistantMessage, UserMessage, TextContent, BaseMessage,
    LLMMessage, Role,
)
from program.session.manager import SessionManager
from program.session.utils import get_latest_compaction_entry
from program.settings.manager import SettingsManager
from program.tool.types import Tool

if TYPE_CHECKING:
    from program.auth.manager import AuthManager
    from program.resource.loader import DefaultResourceLoader


# ============================================================================
# Skill Block Parsing
# ============================================================================

@dataclass
class ParsedSkillBlock:
    name: str
    location: str
    content: str
    user_message: Optional[str]


def parse_skill_block(text: str) -> Optional[ParsedSkillBlock]:
    m = re.match(
        r'^<skill name="([^"]+)" location="([^"]+)">\n([\s\S]*?)\n<\/skill>(?:\n\n([\s\S]+))?$',
        text,
    )
    if not m:
        return None
    return ParsedSkillBlock(
        name=m.group(1),
        location=m.group(2),
        content=m.group(3),
        user_message=(m.group(4) or "").strip() or None,
    )


# ============================================================================
# Event Types
# ============================================================================

AgentSessionEvent = Any  # Union of AgentEvent and session-specific events
AgentSessionEventListener = Callable[[AgentSessionEvent], None]


@dataclass
class ExtensionBindings:
    ui_context: Optional[ExtensionUIContext] = None
    command_context_actions: Optional[ExtensionCommandContextActions] = None
    shutdown_handler: Optional[Callable] = None
    on_error: Optional[ExtensionErrorListener] = None


@dataclass
class PromptOptions:
    expand_prompt_templates: bool = True
    images: Optional[list] = None
    streaming_behavior: Optional[str] = None  # "steer" | "followUp"
    source: Optional[str] = None  # "interactive" | "extension"
    preflight_result: Optional[Callable[[bool], None]] = None


@dataclass
class ModelCycleResult:
    model: Model
    thinking_level: str
    is_scoped: bool


@dataclass
class SessionStats:
    session_file: Optional[str]
    session_id: str
    user_messages: int
    assistant_messages: int
    tool_calls: int
    tool_results: int
    total_messages: int
    tokens: dict
    cost: float
    context_usage: Optional[dict] = None


# ============================================================================
# AgentSessionConfig
# ============================================================================

@dataclass
class AgentSessionConfig:
    cwd: str
    session_manager: SessionManager
    settings_manager: SettingsManager
    resource_loader: "DefaultResourceLoader"
    model_registry: ModelRegistry
    agent_dir: str = ""
    auth_manager: Optional["AuthManager"] = None
    llm: Optional[LLM] = None
    model: Optional[Model] = None
    thinking_level: Optional[str] = None
    scoped_models: list = field(default_factory=list)
    custom_tools: list = field(default_factory=list)
    initial_active_tool_names: Optional[list[str]] = None
    allowed_tool_names: Optional[set[str]] = None
    session_start_event: Optional[dict] = None
    extension_runner_ref: Optional[dict] = None


# ============================================================================
# AgentSession
# ============================================================================

_DEFAULT_TOOL_NAMES = ["read", "bash", "edit", "write"]
_THINKING_LEVELS = ["off", "minimal", "low", "medium", "high"]


class AgentSession:
    """
    Core abstraction for agent lifecycle and session management.

    Shared between all run modes (interactive, print, rpc).
    """

    def __init__(self, config: AgentSessionConfig) -> None:
        self.session_manager = config.session_manager
        self.settings_manager = config.settings_manager

        self._cwd = config.cwd
        self._agent_dir = config.agent_dir
        self._model_registry = config.model_registry
        self._resource_loader = config.resource_loader
        self._custom_tools = list(config.custom_tools)
        self._scoped_models: list = list(config.scoped_models)
        self._initial_active_tool_names = config.initial_active_tool_names
        self._allowed_tool_names = config.allowed_tool_names
        self._session_start_event: dict = config.session_start_event or {
            "type": "session_start", "reason": "startup"
        }
        self._extension_runner_ref = config.extension_runner_ref

        # Model / thinking state
        self._model: Optional[Model] = config.model
        self._thinking_level: str = config.thinking_level or "off"

        # Event subscription
        self._event_listeners: list[AgentSessionEventListener] = []

        # Queue state for UI display
        self._steering_messages: list[str] = []
        self._follow_up_messages: list[str] = []
        self._pending_next_turn_messages: list = []

        # Compaction
        self._compaction_abort_event: Optional[asyncio.Event] = None
        self._auto_compaction_abort_event: Optional[asyncio.Event] = None
        self._overflow_recovery_attempted = False

        # Branch summarization
        self._branch_summary_abort_event: Optional[asyncio.Event] = None

        # Retry state
        self._retry_abort_event: Optional[asyncio.Event] = None
        self._retry_attempt = 0
        self._retry_future: Optional[asyncio.Future] = None

        # Bash execution
        self._bash_task: Optional[asyncio.Task] = None
        self._pending_bash_messages: list = []

        # Extension
        self._extension_ui_context: Optional[ExtensionUIContext] = None
        self._extension_command_context_actions: Optional[ExtensionCommandContextActions] = None
        self._extension_shutdown_handler: Optional[Callable] = None
        self._extension_error_listener: Optional[ExtensionErrorListener] = None
        self._extension_error_unsubscriber: Optional[Callable] = None

        # Turn index for extension events
        self._turn_index = 0

        # Last assistant message for compaction checks
        self._last_assistant_message: Optional[AssistantMessage] = None

        # Build tools and agent
        self._tool_registry: dict[str, Tool] = {}
        self._build_tools()

        # Create LLM service
        llm = config.llm or self._create_llm()
        tools = self._get_active_tools()

        system_prompt = self._build_system_prompt()
        self._base_system_prompt = system_prompt

        self.agent = Agent(
            llm=llm,
            tools=tools,
            system_prompt=system_prompt,
            options=AgentOptions(),
        )
        self.agent.state.is_streaming = False

        # Build and bind extension runner
        self._build_extension_runner()

    # =========================================================================
    # Internal Setup
    # =========================================================================

    def _create_llm(self):
        from program.llm.service import LLM

        model = self._model
        if model:
            try:
                return LLM(model_id=model.id, provider=model.provider)
            except Exception:
                pass

        provider = self.settings_manager.get_default_provider()
        model_id = self.settings_manager.get_default_model()
        if provider and model_id:
            try:
                return LLM(model_id=model_id, provider=provider)
            except Exception:
                pass

        # Fallback: try built-in models
        models = self._model_registry.list()
        if models:
            m = models[0]
            self._model = m
            try:
                return LLM(model_id=m.id, provider=m.provider)
            except Exception:
                pass

        raise RuntimeError("No model available. Configure a provider and model.")

    def _build_tools(self) -> None:
        from program.agent.tools import (
            LsTool, ReadTool, WriteTool, EditTool,
            GrepTool, GlobTool, TerminalTool, WebFetchTool, WebSearchTool,
        )
        all_tools: list[Tool] = [
            LsTool(), ReadTool(), WriteTool(), EditTool(),
            GrepTool(), GlobTool(), TerminalTool(), WebFetchTool(), WebSearchTool(),
        ]
        allowed = self._allowed_tool_names
        for tool in all_tools:
            if allowed is None or tool.name in allowed:
                self._tool_registry[tool.name] = tool

    def _get_active_tools(self) -> list[Tool]:
        if self.agent.state.tools:
            return self.agent.state.tools
        active_names = self._initial_active_tool_names or _DEFAULT_TOOL_NAMES
        tools = []
        for name in active_names:
            t = self._tool_registry.get(name)
            if t:
                tools.append(t)
        return tools

    def _build_system_prompt(self) -> str:
        loader = self._resource_loader
        system_prompt = loader.get_system_prompt()
        if system_prompt:
            return system_prompt
        return ""

    def _build_extension_runner(self) -> None:
        extensions_result: LoadExtensionsResult = self._resource_loader.get_extensions()

        self._extension_runner = ExtensionRunner(
            extensions=extensions_result.extensions,
            runtime=extensions_result.runtime,
            cwd=self._cwd,
            session_manager=self.session_manager,
            model_registry=self._model_registry,
        )
        if self._extension_runner_ref is not None:
            self._extension_runner_ref["current"] = self._extension_runner

        self._bind_extension_core(self._extension_runner)
        self._apply_extension_bindings(self._extension_runner)

    def _bind_extension_core(self, runner: ExtensionRunner) -> None:
        runner.bind_core(
            {
                "send_message": lambda msg, opts=None: asyncio.create_task(
                    self.send_custom_message(msg, opts)
                ),
                "send_user_message": lambda content, opts=None: asyncio.create_task(
                    self.send_user_message(content, opts)
                ),
                "append_entry": lambda custom_type, data=None: self.session_manager.append_custom_entry(
                    custom_type, data
                ),
                "set_session_name": lambda name: self.set_session_name(name),
                "get_session_name": lambda: self.session_manager.get_session_name(),
                "set_label": lambda entry_id, label: self.session_manager.append_label_change(
                    entry_id, label
                ),
                "get_active_tools": self.get_active_tool_names,
                "get_all_tools": self.get_all_tools,
                "set_active_tools": self.set_active_tools_by_name,
                "refresh_tools": self._refresh_tool_registry,
                "get_commands": lambda: [],
                "set_model": self._extension_set_model,
                "get_thinking_level": lambda: self._thinking_level,
                "set_thinking_level": self.set_thinking_level,
            },
            {
                "get_model": lambda: self._model,
                "is_idle": lambda: not self.is_streaming,
                "get_signal": lambda: None,
                "abort": lambda: asyncio.create_task(self.abort()),
                "has_pending_messages": lambda: self.pending_message_count > 0,
                "shutdown": lambda: self._extension_shutdown_handler and self._extension_shutdown_handler(),
                "get_context_usage": self.get_context_usage,
                "compact": lambda opts=None: asyncio.create_task(self._fire_and_forget_compact(opts)),
                "get_system_prompt": lambda: self.system_prompt,
            },
        )

    async def _extension_set_model(self, model: Model) -> bool:
        try:
            await self.set_model(model)
            return True
        except Exception:
            return False

    async def _fire_and_forget_compact(self, options: Optional[dict]) -> None:
        try:
            result = await self.compact(
                options.get("custom_instructions") if options else None
            )
            if options and options.get("on_complete"):
                options["on_complete"](result)
        except Exception as e:
            if options and options.get("on_error"):
                options["on_error"](e)

    def _apply_extension_bindings(self, runner: ExtensionRunner) -> None:
        runner.set_ui_context(self._extension_ui_context)
        runner.bind_command_context(self._extension_command_context_actions)

        if self._extension_error_unsubscriber:
            self._extension_error_unsubscriber()
        if self._extension_error_listener:
            self._extension_error_unsubscriber = runner.on_error(self._extension_error_listener)
        else:
            self._extension_error_unsubscriber = None

    # =========================================================================
    # Event Emission
    # =========================================================================

    def _emit(self, event: AgentSessionEvent) -> None:
        for listener in self._event_listeners:
            listener(event)

    def _emit_queue_update(self) -> None:
        self._emit({
            "type": "queue_update",
            "steering": list(self._steering_messages),
            "follow_up": list(self._follow_up_messages),
        })

    def _handle_agent_event(self, event: AgentEvent) -> None:
        """Synchronous agent event handler - called from agent._loop() emit."""
        # Update agent state (same as original Agent.process_events)
        if isinstance(event, MessageStartEvent):
            self.agent.state.streaming_message = event.message
        elif isinstance(event, MessageUpdateEvent):
            self.agent.state.streaming_message = event.message
        elif isinstance(event, MessageEndEvent):
            self.agent.state.streaming_message = None
            self.agent.state.messages.append(event.message)
        elif isinstance(event, ToolExecutionStartEvent):
            self.agent.state.pending_tool_calls.add(event.tool_call.id)
        elif isinstance(event, ToolExecutionEndEvent):
            self.agent.state.pending_tool_calls.discard(event.tool_result.id)
        elif isinstance(event, AgentErrorEvent):
            self.agent.state.error_message = event.error

        # Session persistence on message_end
        if isinstance(event, MessageEndEvent):
            msg = event.message
            if hasattr(msg, "role"):
                role = getattr(msg, "role", None)
                if role in (Role.USER, Role.ASSISTANT, Role.TOOL):
                    if isinstance(msg, LLMMessage):
                        self.session_manager.append_llm_message(msg)
                elif hasattr(msg, "custom_type"):
                    self.session_manager.append_custom_message_entry(
                        msg.custom_type,
                        getattr(msg, "content", None),
                        getattr(msg, "display", None),
                        getattr(msg, "details", None),
                    )
            if hasattr(msg, "role") and getattr(msg, "role", None) == Role.ASSISTANT:
                self._last_assistant_message = msg

        # Queue state: remove from steering/followUp when delivered
        if isinstance(event, MessageStartEvent):
            msg = event.message
            if hasattr(msg, "role") and getattr(msg, "role", None) == Role.USER:
                self._overflow_recovery_attempted = False
                text = self._get_user_message_text(msg)
                if text in self._steering_messages:
                    self._steering_messages.remove(text)
                    self._emit_queue_update()
                elif text in self._follow_up_messages:
                    self._follow_up_messages.remove(text)
                    self._emit_queue_update()

        # Notify listeners
        self._emit(event)

        # Schedule async post-processing (extension events, compaction checks)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._async_handle_agent_event(event))
        except RuntimeError:
            pass

    async def _async_handle_agent_event(self, event: AgentEvent) -> None:
        """Async follow-up for extension events and compaction checks."""
        await self._emit_extension_event(event)

        if isinstance(event, AgentEndEvent) and self._last_assistant_message is not None:
            msg = self._last_assistant_message
            self._last_assistant_message = None
            if self._is_retryable_error(msg):
                did_retry = await self._handle_retryable_error(msg)
                if did_retry:
                    return
            self._resolve_retry()
            await self._check_compaction(msg)

    # =========================================================================
    # Event Subscription
    # =========================================================================

    def subscribe(self, listener: AgentSessionEventListener) -> Callable[[], None]:
        self._event_listeners.append(listener)

        def unsubscribe():
            try:
                self._event_listeners.remove(listener)
            except ValueError:
                pass

        return unsubscribe

    def dispose(self) -> None:
        self._extension_runner.invalidate(
            "This extension ctx is stale after session replacement or reload."
        )
        self._event_listeners.clear()

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def model_registry(self) -> ModelRegistry:
        return self._model_registry

    @property
    def state(self) -> AgentState:
        return self.agent.state

    @property
    def model(self) -> Optional[Model]:
        return self._model

    @property
    def thinking_level(self) -> str:
        return self._thinking_level

    @property
    def is_streaming(self) -> bool:
        return self.agent.state.is_streaming

    @property
    def system_prompt(self) -> str:
        return self.agent.state.system_prompt or self._base_system_prompt

    @property
    def retry_attempt(self) -> int:
        return self._retry_attempt

    @property
    def messages(self) -> list:
        return self.agent.state.messages

    @property
    def steering_mode(self) -> str:
        return self.settings_manager.get_steering_mode()

    @property
    def follow_up_mode(self) -> str:
        return self.settings_manager.get_follow_up_mode()

    @property
    def session_file(self) -> Optional[str]:
        return self.session_manager.get_session_file()

    @property
    def session_id(self) -> str:
        return self.session_manager.get_session_id()

    @property
    def session_name(self) -> Optional[str]:
        return self.session_manager.get_session_name()

    @property
    def scoped_models(self) -> list:
        return list(self._scoped_models)

    @property
    def prompt_templates(self) -> list:
        return self._resource_loader.get_prompts().get("prompts", [])

    @property
    def resource_loader(self) -> "DefaultResourceLoader":
        return self._resource_loader

    @property
    def is_compacting(self) -> bool:
        return (
            self._compaction_abort_event is not None
            or self._auto_compaction_abort_event is not None
            or self._branch_summary_abort_event is not None
        )

    @property
    def pending_message_count(self) -> int:
        return len(self._steering_messages) + len(self._follow_up_messages)

    @property
    def is_retrying(self) -> bool:
        return self._retry_future is not None

    @property
    def auto_retry_enabled(self) -> bool:
        return self.settings_manager.settings.retry_enabled if hasattr(self.settings_manager.settings, "retry_enabled") else True

    @property
    def auto_compaction_enabled(self) -> bool:
        return self.settings_manager.get_compaction_enabled()

    @property
    def is_bash_running(self) -> bool:
        return self._bash_task is not None and not self._bash_task.done()

    @property
    def has_pending_bash_messages(self) -> bool:
        return len(self._pending_bash_messages) > 0

    @property
    def extension_runner(self) -> ExtensionRunner:
        return self._extension_runner

    # =========================================================================
    # Tool Management
    # =========================================================================

    def get_active_tool_names(self) -> list[str]:
        return [t.name for t in self.agent.state.tools]

    def get_all_tools(self) -> list[dict]:
        return [
            {"name": name, "description": getattr(tool, "description", ""), "parameters": {}}
            for name, tool in self._tool_registry.items()
        ]

    def get_tool_definition(self, name: str) -> Optional[Any]:
        tool = self._tool_registry.get(name)
        if tool:
            return getattr(tool, "to_json", lambda: None)()
        return None

    def set_active_tools_by_name(self, tool_names: list[str]) -> None:
        tools: list[Tool] = []
        for name in tool_names:
            t = self._tool_registry.get(name)
            if t:
                tools.append(t)
        self.agent.state.tools = tools
        self._base_system_prompt = self._build_system_prompt()
        self.agent.state.system_prompt = self._base_system_prompt

    def _refresh_tool_registry(self) -> None:
        self._build_tools()
        current_names = self.get_active_tool_names()
        self.set_active_tools_by_name(current_names)

    def set_scoped_models(self, scoped_models: list) -> None:
        self._scoped_models = list(scoped_models)

    def get_steering_messages(self) -> list[str]:
        return list(self._steering_messages)

    def get_follow_up_messages(self) -> list[str]:
        return list(self._follow_up_messages)

    # =========================================================================
    # Prompting
    # =========================================================================

    async def prompt(self, text: str, options: Optional[PromptOptions] = None) -> None:
        opts = options or PromptOptions()
        preflight = opts.preflight_result

        try:
            if opts.expand_prompt_templates and text.startswith("/"):
                handled = await self._try_execute_extension_command(text)
                if handled:
                    if preflight:
                        preflight(True)
                    return

            current_text = text
            current_images = opts.images

            if self._extension_runner.has_handlers("input"):
                input_result = await self._extension_runner.emit_input(
                    current_text, current_images, opts.source or "interactive"
                )
                if input_result and input_result.get("action") == "handled":
                    if preflight:
                        preflight(True)
                    return
                if input_result and input_result.get("action") == "transform":
                    current_text = input_result.get("text", current_text)
                    current_images = input_result.get("images", current_images)

            expanded_text = current_text
            if opts.expand_prompt_templates:
                expanded_text = self._expand_skill_command(expanded_text)

            if self.is_streaming:
                if not opts.streaming_behavior:
                    raise RuntimeError(
                        "Agent is already processing. Specify streaming_behavior ('steer' or 'followUp')."
                    )
                if opts.streaming_behavior == "followUp":
                    await self._queue_follow_up(expanded_text, current_images)
                else:
                    await self._queue_steer(expanded_text, current_images)
                if preflight:
                    preflight(True)
                return

            self._flush_pending_bash_messages()

            if not self._model:
                raise RuntimeError("No model selected.")

            messages: list[BaseMessage] = []

            content: list = [TextContent(content=expanded_text)]
            if current_images:
                content.extend(current_images)
            messages.append(UserMessage(contents=content))

            for msg in self._pending_next_turn_messages:
                messages.append(msg)
            self._pending_next_turn_messages = []

            result = await self._extension_runner.emit_before_agent_start(
                expanded_text, current_images,
                self._base_system_prompt, {},
            )
            if result and result.get("messages"):
                for custom_msg in result["messages"]:
                    messages.append(custom_msg)

            if result and result.get("system_prompt"):
                self.agent.state.system_prompt = result["system_prompt"]
            else:
                self.agent.state.system_prompt = self._base_system_prompt

        except Exception:
            if preflight:
                preflight(False)
            raise

        if not messages:
            return

        if preflight:
            preflight(True)

        orig_process_events = self.agent.process_events
        self.agent.process_events = self._handle_agent_event
        self.agent.state.is_streaming = True
        try:
            signal = asyncio.Event()
            await self.agent._loop(messages, self._handle_agent_event, signal)
        finally:
            self.agent.state.is_streaming = False
            self.agent.process_events = orig_process_events

        await self._wait_for_retry()

    async def _try_execute_extension_command(self, text: str) -> bool:
        space = text.find(" ")
        cmd_name = text[1:] if space == -1 else text[1:space]
        args = "" if space == -1 else text[space + 1:]
        command = self._extension_runner.get_command(cmd_name)
        if not command:
            return False
        ctx = self._extension_runner.create_command_context()
        try:
            await command.handler(args, ctx)
        except Exception as err:
            self._extension_runner.emit_error({
                "extensionPath": f"command:{cmd_name}",
                "event": "command",
                "error": str(err),
            })
        return True

    def _expand_skill_command(self, text: str) -> str:
        if not text.startswith("/skill:"):
            return text
        space = text.find(" ")
        skill_name = text[7:] if space == -1 else text[7:space]
        args = "" if space == -1 else text[space + 1:].strip()

        skills = self._resource_loader.get_skills().get("skills", [])
        skill = next((s for s in skills if s.name == skill_name), None)
        if not skill:
            return text

        try:
            content = Path(skill.file_path).read_text(encoding="utf-8")
            skill_block = (
                f'<skill name="{skill.name}" location="{skill.file_path}">\n'
                f"References are relative to {dirname(skill.file_path)}.\n\n"
                f"{content.strip()}\n</skill>"
            )
            return f"{skill_block}\n\n{args}" if args else skill_block
        except Exception as err:
            self._extension_runner.emit_error({
                "extensionPath": skill.file_path,
                "event": "skill_expansion",
                "error": str(err),
            })
            return text

    async def steer(self, text: str, images: Optional[list] = None) -> None:
        if text.startswith("/"):
            self._assert_not_extension_command(text)
        expanded = self._expand_skill_command(text)
        await self._queue_steer(expanded, images)

    async def follow_up(self, text: str, images: Optional[list] = None) -> None:
        if text.startswith("/"):
            self._assert_not_extension_command(text)
        expanded = self._expand_skill_command(text)
        await self._queue_follow_up(expanded, images)

    def _assert_not_extension_command(self, text: str) -> None:
        space = text.find(" ")
        cmd_name = text[1:] if space == -1 else text[1:space]
        if self._extension_runner.get_command(cmd_name):
            raise RuntimeError(
                f'Extension command "/{cmd_name}" cannot be queued. '
                "Use prompt() or execute the command when not streaming."
            )

    async def _queue_steer(self, text: str, images: Optional[list] = None) -> None:
        self._steering_messages.append(text)
        self._emit_queue_update()
        content: list = [TextContent(content=text)]
        if images:
            content.extend(images)
        await self.agent.steer(UserMessage(contents=content))

    async def _queue_follow_up(self, text: str, images: Optional[list] = None) -> None:
        self._follow_up_messages.append(text)
        self._emit_queue_update()
        content: list = [TextContent(content=text)]
        if images:
            content.extend(images)
        await self.agent.follow_up(UserMessage(contents=content))

    async def send_custom_message(self, message: dict, options: Optional[dict] = None) -> None:
        opts = options or {}
        deliver_as = opts.get("deliver_as")
        trigger_turn = opts.get("trigger_turn", False)
        custom_msg = {
            "role": "custom",
            "custom_type": message.get("custom_type", ""),
            "content": message.get("content"),
            "display": message.get("display"),
            "details": message.get("details"),
        }
        if deliver_as == "nextTurn":
            self._pending_next_turn_messages.append(custom_msg)
        elif self.is_streaming:
            if deliver_as == "followUp":
                await self.agent.follow_up(custom_msg)
            else:
                await self.agent.steer(custom_msg)
        elif trigger_turn:
            await self.agent.run([custom_msg])
        else:
            self.agent.state.messages.append(custom_msg)
            self.session_manager.append_custom_message_entry(
                message.get("custom_type", ""),
                message.get("content"),
                message.get("display"),
                message.get("details"),
            )

    async def send_user_message(
        self, content: Any, options: Optional[dict] = None
    ) -> None:
        opts = options or {}
        if isinstance(content, str):
            text = content
            images = None
        else:
            texts = []
            images = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        texts.append(part.get("text", ""))
                    else:
                        images.append(part)
                elif hasattr(part, "type"):
                    if part.type == "text":
                        texts.append(getattr(part, "text", ""))
                    else:
                        images.append(part)
            text = "\n".join(texts)
            if not images:
                images = None
        await self.prompt(
            text,
            PromptOptions(
                expand_prompt_templates=False,
                streaming_behavior=opts.get("deliver_as"),
                images=images,
                source="extension",
            ),
        )

    def clear_queue(self) -> dict:
        steering = list(self._steering_messages)
        follow_up = list(self._follow_up_messages)
        self._steering_messages.clear()
        self._follow_up_messages.clear()
        self.agent.clear_all_queues()
        self._emit_queue_update()
        return {"steering": steering, "follow_up": follow_up}

    async def abort(self) -> None:
        self.abort_retry()
        self.agent.clear_all_queues()
        self.agent.state.is_streaming = False

    # =========================================================================
    # Model Management
    # =========================================================================

    async def set_model(self, model: Model) -> None:
        from program.llm.service import LLM
        previous = self._model
        self._model = model

        try:
            new_llm = LLM(model_id=model.id, provider=model.provider)
            self.agent.llm = new_llm
        except Exception as e:
            self._model = previous
            raise RuntimeError(f"No API key for {model.provider}/{model.id}") from e

        self.session_manager.append_model_change(model.provider, model.id)
        self.settings_manager.set_default_model_and_provider(model.provider, model.id)
        self.set_thinking_level(self._thinking_level)

        await self._extension_runner.emit({
            "type": "model_select",
            "model": model,
            "previousModel": previous,
            "source": "set",
        })

    async def cycle_model(self, direction: str = "forward") -> Optional[ModelCycleResult]:
        if self._scoped_models:
            return await self._cycle_scoped_model(direction)
        return await self._cycle_available_model(direction)

    async def _cycle_scoped_model(self, direction: str) -> Optional[ModelCycleResult]:
        models = self._scoped_models
        if len(models) <= 1:
            return None
        current = self._model
        idx = next(
            (i for i, m in enumerate(models) if m.get("model") == current), 0
        )
        n = len(models)
        next_idx = (idx + 1) % n if direction == "forward" else (idx - 1 + n) % n
        next_entry = models[next_idx]
        next_model = next_entry.get("model") or next_entry
        next_thinking = next_entry.get("thinking_level") if isinstance(next_entry, dict) else None
        from program.llm.service import LLM
        previous = self._model
        self._model = next_model
        try:
            self.agent.llm = LLM(model_id=next_model.id, provider=next_model.provider)
        except Exception:
            self._model = previous
            return None
        self.session_manager.append_model_change(next_model.provider, next_model.id)
        self.settings_manager.set_default_model_and_provider(next_model.provider, next_model.id)
        self.set_thinking_level(next_thinking or self._thinking_level)
        await self._extension_runner.emit({
            "type": "model_select", "model": next_model, "previousModel": previous, "source": "cycle"
        })
        return ModelCycleResult(model=next_model, thinking_level=self._thinking_level, is_scoped=True)

    async def _cycle_available_model(self, direction: str) -> Optional[ModelCycleResult]:
        from program.llm.service import LLM
        models = self._model_registry.list()
        if len(models) <= 1:
            return None
        current = self._model
        idx = next((i for i, m in enumerate(models) if m == current), 0)
        n = len(models)
        next_idx = (idx + 1) % n if direction == "forward" else (idx - 1 + n) % n
        next_model = models[next_idx]
        previous = self._model
        self._model = next_model
        try:
            self.agent.llm = LLM(model_id=next_model.id, provider=next_model.provider)
        except Exception:
            self._model = previous
            return None
        self.session_manager.append_model_change(next_model.provider, next_model.id)
        self.settings_manager.set_default_model_and_provider(next_model.provider, next_model.id)
        self.set_thinking_level(self._thinking_level)
        await self._extension_runner.emit({
            "type": "model_select", "model": next_model, "previousModel": previous, "source": "cycle"
        })
        return ModelCycleResult(model=next_model, thinking_level=self._thinking_level, is_scoped=False)

    # =========================================================================
    # Thinking Level
    # =========================================================================

    def set_thinking_level(self, level: str) -> None:
        available = self.get_available_thinking_levels()
        effective = level if level in available else self._clamp_thinking_level(level, available)
        previous = self._thinking_level
        if effective == previous:
            return
        self._thinking_level = effective
        self.session_manager.append_thinking_level_change(effective)
        self.settings_manager.set_default_thinking_level(effective)
        self._emit({"type": "thinking_level_changed", "level": effective})
        asyncio.create_task(
            self._extension_runner.emit({
                "type": "thinking_level_select",
                "level": effective,
                "previousLevel": previous,
            })
        )

    def cycle_thinking_level(self) -> Optional[str]:
        if not self.supports_thinking():
            return None
        levels = self.get_available_thinking_levels()
        idx = levels.index(self._thinking_level) if self._thinking_level in levels else 0
        next_level = levels[(idx + 1) % len(levels)]
        self.set_thinking_level(next_level)
        return next_level

    def get_available_thinking_levels(self) -> list[str]:
        if self._model and getattr(self._model, "thinking", False):
            return _THINKING_LEVELS
        return ["off"]

    def supports_thinking(self) -> bool:
        return bool(self._model and getattr(self._model, "thinking", False))

    def _clamp_thinking_level(self, level: str, available: list[str]) -> str:
        if level in available:
            return level
        lvl_idx = _THINKING_LEVELS.index(level) if level in _THINKING_LEVELS else 0
        for candidate in reversed(_THINKING_LEVELS[:lvl_idx + 1]):
            if candidate in available:
                return candidate
        return available[0]

    # =========================================================================
    # Queue Mode
    # =========================================================================

    def set_steering_mode(self, mode: str) -> None:
        self.settings_manager.set_steering_mode(mode)

    def set_follow_up_mode(self, mode: str) -> None:
        self.settings_manager.set_follow_up_mode(mode)

    # =========================================================================
    # Compaction
    # =========================================================================

    async def compact(self, custom_instructions: Optional[str] = None) -> CompactionResult:
        await self.abort()
        self._compaction_abort_event = asyncio.Event()
        self._emit({"type": "compaction_start", "reason": "manual"})

        try:
            if not self._model:
                raise RuntimeError("No model selected.")

            compact_svc = Compact(
                manager=self.session_manager,
                llm=self.agent.llm,
            )
            preparation = compact_svc.prepare()
            if not preparation:
                entries = self.session_manager.get_entries()
                last = entries[-1] if entries else None
                if last and last.type == "compaction":
                    raise RuntimeError("Already compacted")
                raise RuntimeError("Nothing to compact (session too small)")

            if self._extension_runner.has_handlers("session_before_compact"):
                ext_result = await self._extension_runner.emit({
                    "type": "session_before_compact",
                    "preparation": preparation,
                    "branchEntries": self.session_manager.get_branch(),
                    "customInstructions": custom_instructions,
                })
                if ext_result and ext_result.get("cancel"):
                    raise RuntimeError("Compaction cancelled")

            result = await compact_svc.execute(custom_instructions=custom_instructions)

            if self._compaction_abort_event.is_set():
                raise RuntimeError("Compaction cancelled")

            ctx = self.session_manager.build_session_context()
            self.agent.state.messages = ctx.messages

            self._emit({
                "type": "compaction_end",
                "reason": "manual",
                "result": result,
                "aborted": False,
                "will_retry": False,
            })
            return result

        except Exception as error:
            msg = str(error)
            aborted = "cancelled" in msg.lower() or "aborted" in msg.lower()
            self._emit({
                "type": "compaction_end",
                "reason": "manual",
                "result": None,
                "aborted": aborted,
                "will_retry": False,
                "error_message": None if aborted else f"Compaction failed: {msg}",
            })
            raise
        finally:
            self._compaction_abort_event = None

    def abort_compaction(self) -> None:
        if self._compaction_abort_event:
            self._compaction_abort_event.set()
        if self._auto_compaction_abort_event:
            self._auto_compaction_abort_event.set()

    def abort_branch_summary(self) -> None:
        if self._branch_summary_abort_event:
            self._branch_summary_abort_event.set()

    async def _check_compaction(self, assistant_message: Any, skip_aborted_check: bool = True) -> None:
        if not self.settings_manager.get_compaction_enabled():
            return
        if skip_aborted_check and getattr(assistant_message, "stop_reason", None) == "aborted":
            return

        context_window = getattr(self._model, "context_window", 0) if self._model else 0
        compact_svc = Compact(manager=self.session_manager, llm=self.agent.llm)
        estimate = compact_svc.estimate_context_tokens(self.agent.state.messages)

        compaction_entry = get_latest_compaction_entry(self.session_manager.get_branch())
        if compaction_entry:
            ts = getattr(assistant_message, "timestamp", 0) or 0
            import datetime
            comp_ts = datetime.datetime.fromisoformat(compaction_entry.timestamp).timestamp() * 1000
            if ts <= comp_ts:
                return

        settings = DEFAULT_COMPACTION_SETTINGS
        if should_compact(estimate.tokens, context_window, settings):
            await self._run_auto_compaction("threshold", False)

    async def _run_auto_compaction(self, reason: str, will_retry: bool) -> None:
        self._emit({"type": "compaction_start", "reason": reason})
        self._auto_compaction_abort_event = asyncio.Event()

        try:
            if not self._model:
                self._emit({
                    "type": "compaction_end", "reason": reason,
                    "result": None, "aborted": False, "will_retry": False,
                })
                return

            compact_svc = Compact(manager=self.session_manager, llm=self.agent.llm)
            preparation = compact_svc.prepare()
            if not preparation:
                self._emit({
                    "type": "compaction_end", "reason": reason,
                    "result": None, "aborted": False, "will_retry": False,
                })
                return

            result = await compact_svc.execute()

            if self._auto_compaction_abort_event.is_set():
                self._emit({
                    "type": "compaction_end", "reason": reason,
                    "result": None, "aborted": True, "will_retry": False,
                })
                return

            ctx = self.session_manager.build_session_context()
            self.agent.state.messages = ctx.messages

            self._emit({
                "type": "compaction_end", "reason": reason,
                "result": result, "aborted": False, "will_retry": will_retry,
            })

            if will_retry:
                msgs = self.agent.state.messages
                if msgs and getattr(msgs[-1], "role", None) == Role.ASSISTANT:
                    self.agent.state.messages = msgs[:-1]
                asyncio.get_event_loop().call_later(
                    0.1, lambda: asyncio.create_task(self.agent.run_continue())
                )
        except Exception as error:
            self._emit({
                "type": "compaction_end", "reason": reason,
                "result": None, "aborted": False, "will_retry": False,
                "error_message": f"Auto-compaction failed: {error}",
            })
        finally:
            self._auto_compaction_abort_event = None

    def set_auto_compaction_enabled(self, enabled: bool) -> None:
        self.settings_manager.set_compaction_enabled(enabled)

    # =========================================================================
    # Extension Binding
    # =========================================================================

    async def bind_extensions(self, bindings: ExtensionBindings) -> None:
        if bindings.ui_context is not None:
            self._extension_ui_context = bindings.ui_context
        if bindings.command_context_actions is not None:
            self._extension_command_context_actions = bindings.command_context_actions
        if bindings.shutdown_handler is not None:
            self._extension_shutdown_handler = bindings.shutdown_handler
        if bindings.on_error is not None:
            self._extension_error_listener = bindings.on_error

        self._apply_extension_bindings(self._extension_runner)
        await self._extension_runner.emit(self._session_start_event)

    # =========================================================================
    # Reload
    # =========================================================================

    async def reload(self) -> None:
        prev_flag_values = self._extension_runner.get_flag_values()
        await emit_session_shutdown_event(
            self._extension_runner, {"type": "session_shutdown", "reason": "reload"}
        )
        await self.settings_manager.reload()
        await self._resource_loader.reload()

        active_tool_names = self.get_active_tool_names()
        self._build_extension_runner()
        for name, value in prev_flag_values.items():
            self._extension_runner.set_flag_value(name, value)
        self.set_active_tools_by_name(active_tool_names)

        has_bindings = (
            self._extension_ui_context
            or self._extension_command_context_actions
            or self._extension_shutdown_handler
            or self._extension_error_listener
        )
        if has_bindings:
            self._apply_extension_bindings(self._extension_runner)
            await self._extension_runner.emit({"type": "session_start", "reason": "reload"})

    # =========================================================================
    # Auto-Retry
    # =========================================================================

    def _is_retryable_error(self, message: Any) -> bool:
        if getattr(message, "stop_reason", None) != "error":
            return False
        err = getattr(message, "error", "") or ""
        if not err:
            return False
        return bool(re.search(
            r"overloaded|provider.?returned.?error|rate.?limit|too many requests"
            r"|429|500|502|503|504|service.?unavailable|server.?error|internal.?error"
            r"|network.?error|connection.?error|connection.?refused|connection.?lost"
            r"|fetch failed|timed?.?out|timeout|terminated|retry delay",
            err, re.IGNORECASE,
        ))

    async def _handle_retryable_error(self, message: Any) -> bool:
        max_retries = 3
        base_delay_ms = 1000

        self._retry_attempt += 1
        if self._retry_attempt > max_retries:
            self._emit({
                "type": "auto_retry_end",
                "success": False,
                "attempt": self._retry_attempt - 1,
                "final_error": getattr(message, "error", None),
            })
            self._retry_attempt = 0
            self._resolve_retry()
            return False

        delay_s = (base_delay_ms * (2 ** (self._retry_attempt - 1))) / 1000
        self._emit({
            "type": "auto_retry_start",
            "attempt": self._retry_attempt,
            "max_attempts": max_retries,
            "delay_ms": int(delay_s * 1000),
            "error_message": getattr(message, "error", "Unknown error"),
        })

        msgs = self.agent.state.messages
        if msgs and getattr(msgs[-1], "role", None) == Role.ASSISTANT:
            self.agent.state.messages = msgs[:-1]

        self._retry_abort_event = asyncio.Event()
        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.get_event_loop().run_in_executor(None, self._retry_abort_event.wait)),
                timeout=delay_s,
            )
            # Abort event was set during sleep
            attempt = self._retry_attempt
            self._retry_attempt = 0
            self._emit({
                "type": "auto_retry_end", "success": False,
                "attempt": attempt, "final_error": "Retry cancelled",
            })
            self._resolve_retry()
            return False
        except asyncio.TimeoutError:
            pass
        finally:
            self._retry_abort_event = None

        asyncio.get_event_loop().call_soon(
            lambda: asyncio.create_task(self.agent.run_continue())
        )
        return True

    def abort_retry(self) -> None:
        if self._retry_abort_event:
            self._retry_abort_event.set()
        self._resolve_retry()

    def _resolve_retry(self) -> None:
        if self._retry_future and not self._retry_future.done():
            self._retry_future.set_result(None)
        self._retry_future = None

    async def _wait_for_retry(self) -> None:
        if self._retry_future:
            await self._retry_future

    def set_auto_retry_enabled(self, enabled: bool) -> None:
        if hasattr(self.settings_manager.settings, "retry_enabled"):
            self.settings_manager.settings.retry_enabled = enabled

    # =========================================================================
    # Bash Execution
    # =========================================================================

    async def execute_bash(
        self,
        command: str,
        on_chunk: Optional[Callable[[str], None]] = None,
        options: Optional[dict] = None,
    ) -> dict:
        opts = options or {}
        exclude = opts.get("exclude_from_context", False)

        import subprocess
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.session_manager.get_cwd(),
            )
            output_chunks: list[str] = []
            if proc.stdout:
                async for line in proc.stdout:
                    chunk = line.decode(errors="replace")
                    output_chunks.append(chunk)
                    if on_chunk:
                        on_chunk(chunk)
            await proc.wait()
            output = "".join(output_chunks)
            result = {
                "output": output,
                "exit_code": proc.returncode or 0,
                "cancelled": False,
                "truncated": False,
            }
        except Exception as e:
            result = {"output": str(e), "exit_code": 1, "cancelled": False, "truncated": False}

        self.record_bash_result(command, result, options)
        return result

    def record_bash_result(self, command: str, result: dict, options: Optional[dict] = None) -> None:
        opts = options or {}
        bash_msg = {
            "role": "bash_execution",
            "command": command,
            "output": result.get("output", ""),
            "exit_code": result.get("exit_code", 0),
            "cancelled": result.get("cancelled", False),
            "truncated": result.get("truncated", False),
            "exclude_from_context": opts.get("exclude_from_context", False),
        }
        if self.is_streaming:
            self._pending_bash_messages.append(bash_msg)
        else:
            self.agent.state.messages.append(bash_msg)

    def abort_bash(self) -> None:
        if self._bash_task and not self._bash_task.done():
            self._bash_task.cancel()

    def _flush_pending_bash_messages(self) -> None:
        for msg in self._pending_bash_messages:
            self.agent.state.messages.append(msg)
        self._pending_bash_messages.clear()

    # =========================================================================
    # Session Management
    # =========================================================================

    def set_session_name(self, name: str) -> None:
        self.session_manager.append_session_info(name)
        self._emit({
            "type": "session_info_changed",
            "name": self.session_manager.get_session_name(),
        })

    # =========================================================================
    # Tree Navigation
    # =========================================================================

    async def navigate_tree(self, target_id: str, options: Optional[dict] = None) -> dict:
        opts = options or {}
        old_leaf_id = self.session_manager.get_leaf_id()

        if target_id == old_leaf_id:
            return {"cancelled": False}

        target_entry = self.session_manager.get_entry(target_id)
        if not target_entry:
            raise ValueError(f"Entry {target_id} not found")

        branch_compact = BranchCompact(self.session_manager, self.agent.llm)
        collect_result = branch_compact.collect_entries(old_leaf_id or "", target_id)
        entries_to_summarize = collect_result.entries if collect_result else []
        common_ancestor_id = collect_result.common_ancestor_id if collect_result else None

        self._branch_summary_abort_event = asyncio.Event()

        try:
            summary_text: Optional[str] = None
            summary_details = None
            from_extension = False

            if opts.get("summarize") and entries_to_summarize:
                if not self._model:
                    raise RuntimeError("No model available for summarization")
                summary_result = await branch_compact.generate_summary(entries_to_summarize)
                if getattr(summary_result, "aborted", False):
                    return {"cancelled": True, "aborted": True}
                if getattr(summary_result, "error", None):
                    raise RuntimeError(summary_result.error)
                summary_text = getattr(summary_result, "summary", None)

            if target_entry.type == "message" and target_entry.message.role == "user":
                new_leaf_id = target_entry.parent_id
                editor_text = self._extract_user_message_text(target_entry.message.content)
            elif target_entry.type == "custom_message":
                new_leaf_id = target_entry.parent_id
                editor_text = str(target_entry.content or "")
            else:
                new_leaf_id = target_id
                editor_text = None

            summary_entry = None
            label = opts.get("label")

            if summary_text:
                summary_id = self.session_manager.branch_with_summary(
                    new_leaf_id, summary_text, summary_details, from_extension
                )
                summary_entry = self.session_manager.get_entry(summary_id)
                if label:
                    self.session_manager.append_label_change(summary_id, label)
            elif new_leaf_id is None:
                self.session_manager.reset_leaf()
            else:
                self.session_manager.branch(new_leaf_id)

            if label and not summary_text:
                self.session_manager.append_label_change(target_id, label)

            ctx = self.session_manager.build_session_context()
            self.agent.state.messages = ctx.messages

            await self._extension_runner.emit({
                "type": "session_tree",
                "newLeafId": self.session_manager.get_leaf_id(),
                "oldLeafId": old_leaf_id,
                "summaryEntry": summary_entry,
                "fromExtension": from_extension if summary_text else None,
            })

            return {"editor_text": editor_text, "cancelled": False, "summary_entry": summary_entry}
        finally:
            self._branch_summary_abort_event = None

    def get_user_messages_for_forking(self) -> list[dict]:
        result = []
        for entry in self.session_manager.get_entries():
            if entry.type != "message":
                continue
            if entry.message.role != "user":
                continue
            text = self._extract_user_message_text(entry.message.content)
            if text:
                result.append({"entry_id": entry.id, "text": text})
        return result

    def _extract_user_message_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    texts.append(c.get("text", ""))
                elif hasattr(c, "type") and c.type == "text":
                    texts.append(getattr(c, "content", "") or getattr(c, "text", ""))
            return "".join(texts)
        return ""

    # =========================================================================
    # Statistics & Context
    # =========================================================================

    def get_session_stats(self) -> SessionStats:
        msgs = self.agent.state.messages
        user_count = sum(1 for m in msgs if getattr(m, "role", None) == Role.USER)
        asst_count = sum(1 for m in msgs if getattr(m, "role", None) == Role.ASSISTANT)
        tool_results = sum(1 for m in msgs if getattr(m, "role", None) == Role.TOOL)
        tool_calls = 0
        total_input = total_output = total_cache_read = total_cache_write = 0.0
        total_cost = 0.0

        for msg in msgs:
            if getattr(msg, "role", None) == Role.ASSISTANT:
                usage = getattr(msg, "usage", None)
                if usage:
                    tool_calls += len([
                        c for c in getattr(msg, "contents", [])
                        if hasattr(c, "type") and c.type == "tool_call"
                    ])
                    total_input += getattr(usage, "input_tokens", 0)
                    total_output += getattr(usage, "output_tokens", 0)
                    total_cache_read += getattr(usage, "cache_read_tokens", 0)
                    total_cache_write += getattr(usage, "cache_write_tokens", 0)
                    cost = getattr(usage, "cost", None)
                    if cost:
                        total_cost += getattr(cost, "total", 0)

        return SessionStats(
            session_file=self.session_file,
            session_id=self.session_id,
            user_messages=user_count,
            assistant_messages=asst_count,
            tool_calls=tool_calls,
            tool_results=tool_results,
            total_messages=len(msgs),
            tokens={
                "input": int(total_input),
                "output": int(total_output),
                "cache_read": int(total_cache_read),
                "cache_write": int(total_cache_write),
                "total": int(total_input + total_output + total_cache_read + total_cache_write),
            },
            cost=total_cost,
            context_usage=self.get_context_usage(),
        )

    def get_context_usage(self) -> Optional[dict]:
        model = self._model
        if not model:
            return None
        context_window = getattr(model, "context_window", 0)
        if context_window <= 0:
            return None

        compact_svc = Compact(manager=self.session_manager, llm=self.agent.llm)
        estimate = compact_svc.estimate_context_tokens(self.agent.state.messages)
        percent = (estimate.tokens / context_window) * 100
        return {"tokens": estimate.tokens, "context_window": context_window, "percent": percent}

    # =========================================================================
    # Export
    # =========================================================================

    def export_to_jsonl(self, output_path: Optional[str] = None) -> str:
        from datetime import datetime as dt
        from program.session.manager import CURRENT_SESSION_VERSION

        file_path = str(
            Path(output_path).resolve()
            if output_path
            else Path(f"session-{dt.now().isoformat().replace(':', '-')}.jsonl")
        )
        dir_path = Path(file_path).parent
        dir_path.mkdir(parents=True, exist_ok=True)

        header = {
            "type": "session",
            "version": CURRENT_SESSION_VERSION,
            "id": self.session_manager.get_session_id(),
            "timestamp": dt.now().isoformat(),
            "cwd": self.session_manager.get_cwd(),
        }
        branch_entries = self.session_manager.get_branch()
        lines = [json.dumps(header)]
        prev_id = None
        for entry in branch_entries:
            try:
                data = entry.__dict__.copy()
            except AttributeError:
                continue
            data["parent_id"] = prev_id
            lines.append(json.dumps(data))
            prev_id = entry.id

        Path(file_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return file_path

    # =========================================================================
    # Utilities
    # =========================================================================

    def get_last_assistant_text(self) -> Optional[str]:
        for msg in reversed(self.agent.state.messages):
            if getattr(msg, "role", None) != Role.ASSISTANT:
                continue
            texts = []
            for c in getattr(msg, "contents", []):
                if hasattr(c, "type") and c.type == "text":
                    texts.append(getattr(c, "content", "") or getattr(c, "text", ""))
            text = "".join(texts).strip()
            if text:
                return text
        return None

    def _get_user_message_text(self, message: Any) -> str:
        content = getattr(message, "contents", None) or getattr(message, "content", None)
        if not content:
            return ""
        if isinstance(content, str):
            return content
        texts = []
        for c in content:
            if hasattr(c, "type") and c.type == "text":
                texts.append(getattr(c, "content", "") or getattr(c, "text", ""))
            elif isinstance(c, dict) and c.get("type") == "text":
                texts.append(c.get("text", "") or c.get("content", ""))
        return "".join(texts)

    # =========================================================================
    # Extension System
    # =========================================================================

    def create_replaced_session_context(self) -> Any:
        ctx = self._extension_runner.create_command_context()
        return ctx

    def has_extension_handlers(self, event_type: str) -> bool:
        return self._extension_runner.has_handlers(event_type)

    # =========================================================================
    # Internal Extension Event Dispatch
    # =========================================================================

    async def _emit_extension_event(self, event: AgentEvent) -> None:
        runner = self._extension_runner
        if isinstance(event, AgentStartEvent):
            self._turn_index = 0
            await runner.emit({"type": "agent_start"})
        elif isinstance(event, AgentEndEvent):
            await runner.emit({"type": "agent_end", "messages": event.messages})
        elif isinstance(event, TurnStartEvent):
            await runner.emit({
                "type": "turn_start",
                "turnIndex": self._turn_index,
                "timestamp": int(asyncio.get_event_loop().time() * 1000),
            })
        elif isinstance(event, TurnEndEvent):
            await runner.emit({
                "type": "turn_end",
                "turnIndex": self._turn_index,
                "message": event.message,
                "toolResults": event.tool_results,
            })
            self._turn_index += 1
        elif isinstance(event, MessageStartEvent):
            await runner.emit({"type": "message_start", "message": event.message})
        elif isinstance(event, MessageUpdateEvent):
            await runner.emit({
                "type": "message_update",
                "message": event.message,
                "assistantMessageEvent": None,
            })
        elif isinstance(event, MessageEndEvent):
            await runner.emit_message_end({"type": "message_end", "message": event.message})
        elif isinstance(event, ToolExecutionStartEvent):
            await runner.emit({
                "type": "tool_execution_start",
                "toolCallId": event.tool_call.id,
                "toolName": event.tool_call.name,
                "args": event.tool_call.args,
            })
        elif isinstance(event, ToolExecutionEndEvent):
            await runner.emit({
                "type": "tool_execution_end",
                "toolCallId": event.tool_result.id,
                "toolName": event.tool_result.name if hasattr(event.tool_result, "name") else "",
                "result": event.tool_result,
                "isError": getattr(event.tool_result, "is_error", False),
            })

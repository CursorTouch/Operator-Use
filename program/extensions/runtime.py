from __future__ import annotations
import copy
import logging
from typing import Any, Callable, Literal, Optional, Union

from program.extensions.types import (
    AbortSignal,
    BeforeAgentStartEvent,
    BeforeAgentStartEventResult,
    BeforeProviderRequestEvent,
    CompactOptions,
    ContextEvent,
    ContextEventResult,
    ContextUsage,
    Extension,
    ExtensionActions,
    ExtensionCommandContextActions,
    ExtensionCommandContext,
    ExtensionContext,
    ExtensionContextActions,
    ExtensionError,
    ExtensionFlag,
    ExtensionRuntime,
    InputEvent,
    InputEventContinueResult,
    InputEventHandledResult,
    InputEventResult,
    InputEventTransformResult,
    InputSource,
    MessageEndEvent,
    MessageEndEventResult,
    ProviderConfig,
    RegisteredCommand,
    RegisteredTool,
    ReplacedSessionContext,
    ResolvedCommand,
    ResourcesDiscoverEvent,
    ResourcesDiscoverResult,
    SessionBeforeCompactResult,
    SessionBeforeForkResult,
    SessionBeforeSwitchResult,
    SessionBeforeTreeResult,
    SessionShutdownEvent,
    ToolCallEvent,
    ToolCallEventResult,
    ToolInfo,
    ToolResultEvent,
    ToolResultEventResult,
    UserBashEvent,
    UserBashEventResult,
)
from program.llm.model.types import Model
from program.llm.types import ThinkingLevel
from program.message.types import BaseMessage, ImageContent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Concrete ExtensionContext backed by a runner
# ---------------------------------------------------------------------------

class RunnerExtensionContext(ExtensionContext):
    def __init__(self, runner: ExtensionRunner) -> None:
        self._runner = runner

    def _check(self) -> None:
        self._runner._assert_active()

    @property
    def cwd(self) -> str:
        self._check()
        return self._runner._cwd

    @property
    def session_manager(self):
        self._check()
        return self._runner._session_manager

    @property
    def model_registry(self):
        self._check()
        return self._runner._model_registry

    @property
    def model(self) -> Optional[Model]:
        self._check()
        return self._runner._get_model()

    def is_idle(self) -> bool:
        self._check()
        return self._runner._is_idle_fn()

    @property
    def signal(self) -> Optional[AbortSignal]:
        self._check()
        return self._runner._get_signal_fn()

    def abort(self) -> None:
        self._check()
        self._runner._abort_fn()

    def has_pending_messages(self) -> bool:
        self._check()
        return self._runner._has_pending_messages_fn()

    def shutdown(self) -> None:
        self._check()
        self._runner._shutdown_handler()

    def get_context_usage(self) -> Optional[ContextUsage]:
        self._check()
        return self._runner._get_context_usage_fn()

    def compact(self, options: Optional[CompactOptions] = None) -> None:
        self._check()
        self._runner._compact_fn(options)

    def get_system_prompt(self) -> str:
        self._check()
        return self._runner._get_system_prompt_fn()


class RunnerCommandContext(RunnerExtensionContext):
    async def wait_for_idle(self) -> None:
        self._check()
        await self._runner._wait_for_idle_fn()

    async def new_session(self, options=None) -> dict:
        self._check()
        return await self._runner._new_session_handler(options)

    async def fork(self, entry_id: str, options=None) -> dict:
        self._check()
        return await self._runner._fork_handler(entry_id, options)

    async def navigate_tree(self, target_id: str, options=None) -> dict:
        self._check()
        return await self._runner._navigate_tree_handler(target_id, options)

    async def switch_session(self, session_path: str, options=None) -> dict:
        self._check()
        return await self._runner._switch_session_handler(session_path, options)

    async def reload(self) -> None:
        self._check()
        await self._runner._reload_handler()


ExtensionErrorListener = Callable[[ExtensionError], None]


# ---------------------------------------------------------------------------
# ExtensionRunner
# ---------------------------------------------------------------------------

class ExtensionRunner:
    def __init__(
        self,
        extensions: list[Extension],
        runtime: ExtensionRuntime,
        cwd: str,
        session_manager: Any,
        model_registry: Any,
    ) -> None:
        self._extensions = extensions
        self._runtime = runtime
        self._cwd = cwd
        self._session_manager = session_manager
        self._model_registry = model_registry

        self._error_listeners: set[ExtensionErrorListener] = set()
        self._stale_message: Optional[str] = None
        self._command_diagnostics: list[dict] = []

        # Callback fields (bound via bind_core / bind_command_context)
        self._get_model: Callable[[], Optional[Model]] = lambda: None
        self._is_idle_fn: Callable[[], bool] = lambda: True
        self._get_signal_fn: Callable[[], Optional[AbortSignal]] = lambda: None
        self._abort_fn: Callable[[], None] = lambda: None
        self._has_pending_messages_fn: Callable[[], bool] = lambda: False
        self._get_context_usage_fn: Callable[[], Optional[ContextUsage]] = lambda: None
        self._compact_fn: Callable[..., None] = lambda *a, **kw: None
        self._get_system_prompt_fn: Callable[[], str] = lambda: ""
        self._shutdown_handler: Callable[[], None] = lambda: None

        self._wait_for_idle_fn: Callable[[], Any] = _async_noop
        self._new_session_handler: Callable[..., Any] = _cancelled_false
        self._fork_handler: Callable[..., Any] = _cancelled_false
        self._navigate_tree_handler: Callable[..., Any] = _cancelled_false
        self._switch_session_handler: Callable[..., Any] = _cancelled_false
        self._reload_handler: Callable[[], Any] = _async_noop

    # -------------------------------------------------------------------------
    # Binding
    # -------------------------------------------------------------------------

    def bind_core(
        self,
        actions: ExtensionActions,
        context_actions: ExtensionContextActions,
        provider_actions: Optional[dict] = None,
    ) -> None:
        # Wire action implementations into the shared runtime
        self._runtime.send_message = actions.send_message
        self._runtime.send_user_message = actions.send_user_message
        self._runtime.append_entry = actions.append_entry
        self._runtime.set_session_name = actions.set_session_name
        self._runtime.get_session_name = actions.get_session_name
        self._runtime.set_label = actions.set_label
        self._runtime.get_active_tools = actions.get_active_tools
        self._runtime.get_all_tools = actions.get_all_tools
        self._runtime.set_active_tools = actions.set_active_tools
        self._runtime.refresh_tools = actions.refresh_tools
        self._runtime.get_commands = actions.get_commands
        self._runtime.set_model = actions.set_model
        self._runtime.get_thinking_level = actions.get_thinking_level
        self._runtime.set_thinking_level = actions.set_thinking_level

        # Context actions
        self._get_model = context_actions.get_model
        self._is_idle_fn = context_actions.is_idle
        self._get_signal_fn = context_actions.get_signal
        self._abort_fn = context_actions.abort
        self._has_pending_messages_fn = context_actions.has_pending_messages
        self._shutdown_handler = context_actions.shutdown
        self._get_context_usage_fn = context_actions.get_context_usage
        self._compact_fn = context_actions.compact
        self._get_system_prompt_fn = context_actions.get_system_prompt

        # Flush queued provider registrations
        register_fn = (
            (provider_actions or {}).get("register_provider")
            or getattr(self._model_registry, "register_provider", None)
        )
        for entry in self._runtime.pending_provider_registrations:
            try:
                if register_fn:
                    register_fn(entry["name"], entry["config"])
            except Exception as exc:
                self.emit_error(ExtensionError(
                    extension_path=entry.get("extension_path", ""),
                    event="register_provider",
                    error=str(exc),
                ))
        self._runtime.pending_provider_registrations = []

        # After bind, provider registration takes effect immediately
        unregister_fn = (
            (provider_actions or {}).get("unregister_provider")
            or getattr(self._model_registry, "unregister_provider", None)
        )

        def _register(name: str, config: Any, ext_path: Optional[str] = None) -> None:
            if register_fn:
                register_fn(name, config)
            else:
                logger.warning("No register_provider implementation available")

        def _unregister(name: str, ext_path: Optional[str] = None) -> None:
            if unregister_fn:
                unregister_fn(name)
            else:
                logger.warning("No unregister_provider implementation available")

        self._runtime.register_provider = _register
        self._runtime.unregister_provider = _unregister

    def bind_command_context(self, actions: Optional[ExtensionCommandContextActions] = None) -> None:
        if actions is not None:
            self._wait_for_idle_fn = actions.wait_for_idle
            self._new_session_handler = actions.new_session
            self._fork_handler = actions.fork
            self._navigate_tree_handler = actions.navigate_tree
            self._switch_session_handler = actions.switch_session
            self._reload_handler = actions.reload
        else:
            self._wait_for_idle_fn = _async_noop
            self._new_session_handler = _cancelled_false
            self._fork_handler = _cancelled_false
            self._navigate_tree_handler = _cancelled_false
            self._switch_session_handler = _cancelled_false
            self._reload_handler = _async_noop

    # -------------------------------------------------------------------------
    # Extension queries
    # -------------------------------------------------------------------------

    def get_extension_paths(self) -> list[str]:
        return [ext.path for ext in self._extensions]

    def get_all_registered_tools(self) -> list[RegisteredTool]:
        seen: dict[str, RegisteredTool] = {}
        for ext in self._extensions:
            for name, tool in ext.tools.items():
                if name not in seen:
                    seen[name] = tool
        return list(seen.values())

    def get_tool_definition(self, tool_name: str):
        for ext in self._extensions:
            tool = ext.tools.get(tool_name)
            if tool:
                return tool.definition
        return None

    def get_flags(self) -> dict[str, ExtensionFlag]:
        all_flags: dict[str, ExtensionFlag] = {}
        for ext in self._extensions:
            for name, flag in ext.flags.items():
                if name not in all_flags:
                    all_flags[name] = flag
        return all_flags

    def set_flag_value(self, name: str, value: Union[bool, str]) -> None:
        self._runtime.flag_values[name] = value

    def get_flag_values(self) -> dict[str, Union[bool, str]]:
        return dict(self._runtime.flag_values)

    # -------------------------------------------------------------------------
    # Stale instance handling
    # -------------------------------------------------------------------------

    def invalidate(self, message: Optional[str] = None) -> None:
        if not self._stale_message:
            self._stale_message = message or (
                "This extension ctx is stale after session replacement or reload. "
                "Do not use a captured pi or command ctx after ctx.new_session(), "
                "ctx.fork(), ctx.switch_session(), or ctx.reload()."
            )
            self._runtime.invalidate(self._stale_message)

    def _assert_active(self) -> None:
        if self._stale_message:
            raise RuntimeError(self._stale_message)

    # -------------------------------------------------------------------------
    # Error listeners
    # -------------------------------------------------------------------------

    def on_error(self, listener: ExtensionErrorListener) -> Callable[[], None]:
        self._error_listeners.add(listener)
        return lambda: self._error_listeners.discard(listener)

    def emit_error(self, error: ExtensionError) -> None:
        for listener in self._error_listeners:
            listener(error)

    # -------------------------------------------------------------------------
    # Handler / renderer queries
    # -------------------------------------------------------------------------

    def has_handlers(self, event_type: str) -> bool:
        for ext in self._extensions:
            handlers = ext.handlers.get(event_type)
            if handlers:
                return True
        return False

    # -------------------------------------------------------------------------
    # Command resolution
    # -------------------------------------------------------------------------

    def _resolve_registered_commands(self) -> list[ResolvedCommand]:
        commands: list[RegisteredCommand] = []
        counts: dict[str, int] = {}

        for ext in self._extensions:
            for command in ext.commands.values():
                commands.append(command)
                counts[command.name] = counts.get(command.name, 0) + 1

        seen: dict[str, int] = {}
        taken: set[str] = set()
        resolved: list[ResolvedCommand] = []

        for command in commands:
            occurrence = seen.get(command.name, 0) + 1
            seen[command.name] = occurrence

            if counts.get(command.name, 0) > 1:
                invocation_name = f"{command.name}:{occurrence}"
            else:
                invocation_name = command.name

            if invocation_name in taken:
                suffix = occurrence
                while invocation_name in taken:
                    suffix += 1
                    invocation_name = f"{command.name}:{suffix}"

            taken.add(invocation_name)
            rc = ResolvedCommand(
                name=command.name,
                handler=command.handler,
                source_info=command.source_info,
                description=command.description,
                get_argument_completions=command.get_argument_completions,
                invocation_name=invocation_name,
            )
            resolved.append(rc)

        return resolved

    def get_registered_commands(self) -> list[ResolvedCommand]:
        self._command_diagnostics = []
        return self._resolve_registered_commands()

    def get_command_diagnostics(self) -> list[dict]:
        return self._command_diagnostics

    def get_command(self, name: str) -> Optional[ResolvedCommand]:
        return next(
            (c for c in self._resolve_registered_commands() if c.invocation_name == name),
            None,
        )

    def shutdown(self) -> None:
        self._shutdown_handler()

    # -------------------------------------------------------------------------
    # Context factories
    # -------------------------------------------------------------------------

    def create_context(self) -> RunnerExtensionContext:
        return RunnerExtensionContext(self)

    def create_command_context(self) -> RunnerCommandContext:
        return RunnerCommandContext(self)

    # -------------------------------------------------------------------------
    # Event emission
    # -------------------------------------------------------------------------

    async def emit(self, event: Any) -> Any:
        """
        Generic emit for most events. Returns the first non-None result from
        session_before_* handlers (short-circuits on cancel); None otherwise.
        """
        ctx = self.create_context()
        session_before_types = {
            "session_before_switch",
            "session_before_fork",
            "session_before_compact",
            "session_before_tree",
        }
        is_before = event.type in session_before_types
        result = None

        for ext in self._extensions:
            handlers = ext.handlers.get(event.type, [])
            for handler in handlers:
                try:
                    handler_result = await _call_handler(handler, event, ctx)
                    if is_before and handler_result:
                        result = handler_result
                        if getattr(result, "cancel", None) or (isinstance(result, dict) and result.get("cancel")):
                            return result
                except Exception as exc:
                    self.emit_error(ExtensionError(
                        extension_path=ext.path,
                        event=event.type,
                        error=str(exc),
                        stack=_get_stack(exc),
                    ))

        return result

    async def emit_message_end(self, event: MessageEndEvent) -> Optional[BaseMessage]:
        ctx = self.create_context()
        current_message = event.message
        modified = False

        for ext in self._extensions:
            handlers = ext.handlers.get("message_end", [])
            for handler in handlers:
                try:
                    current_event = MessageEndEvent(message=current_message)
                    raw = await _call_handler(handler, current_event, ctx)
                    result: Optional[MessageEndEventResult] = raw
                    if not result or not result.message:
                        continue
                    if result.message.role != current_message.role:
                        self.emit_error(ExtensionError(
                            extension_path=ext.path,
                            event="message_end",
                            error="message_end handlers must return a message with the same role",
                        ))
                        continue
                    current_message = result.message
                    modified = True
                except Exception as exc:
                    self.emit_error(ExtensionError(
                        extension_path=ext.path,
                        event="message_end",
                        error=str(exc),
                        stack=_get_stack(exc),
                    ))

        return current_message if modified else None

    async def emit_tool_result(self, event: ToolResultEvent) -> Optional[ToolResultEventResult]:
        ctx = self.create_context()
        current = _shallow_copy_event(event)
        modified = False

        for ext in self._extensions:
            handlers = ext.handlers.get("tool_result", [])
            for handler in handlers:
                try:
                    raw = await _call_handler(handler, current, ctx)
                    result: Optional[ToolResultEventResult] = raw
                    if not result:
                        continue
                    if result.content is not None:
                        current.content = result.content
                        modified = True
                    if result.details is not None:
                        current.details = result.details
                        modified = True
                    if result.is_error is not None:
                        current.is_error = result.is_error
                        modified = True
                except Exception as exc:
                    self.emit_error(ExtensionError(
                        extension_path=ext.path,
                        event="tool_result",
                        error=str(exc),
                        stack=_get_stack(exc),
                    ))

        if not modified:
            return None
        return ToolResultEventResult(
            content=current.content,
            details=current.details,
            is_error=current.is_error,
        )

    async def emit_tool_call(self, event: ToolCallEvent) -> Optional[ToolCallEventResult]:
        ctx = self.create_context()
        result: Optional[ToolCallEventResult] = None

        for ext in self._extensions:
            handlers = ext.handlers.get("tool_call", [])
            for handler in handlers:
                raw = await _call_handler(handler, event, ctx)
                if raw:
                    result = raw
                    if getattr(result, "block", None) or (isinstance(result, dict) and result.get("block")):
                        return result

        return result

    async def emit_user_bash(self, event: UserBashEvent) -> Optional[UserBashEventResult]:
        ctx = self.create_context()

        for ext in self._extensions:
            handlers = ext.handlers.get("user_bash", [])
            for handler in handlers:
                try:
                    raw = await _call_handler(handler, event, ctx)
                    if raw:
                        return raw
                except Exception as exc:
                    self.emit_error(ExtensionError(
                        extension_path=ext.path,
                        event="user_bash",
                        error=str(exc),
                        stack=_get_stack(exc),
                    ))

        return None

    async def emit_context(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        ctx = self.create_context()
        current_messages = copy.deepcopy(messages)

        for ext in self._extensions:
            handlers = ext.handlers.get("context", [])
            for handler in handlers:
                try:
                    event = ContextEvent(messages=current_messages)
                    raw = await _call_handler(handler, event, ctx)
                    result: Optional[ContextEventResult] = raw
                    if result and result.messages is not None:
                        current_messages = result.messages
                except Exception as exc:
                    self.emit_error(ExtensionError(
                        extension_path=ext.path,
                        event="context",
                        error=str(exc),
                        stack=_get_stack(exc),
                    ))

        return current_messages

    async def emit_before_provider_request(self, payload: Any) -> Any:
        ctx = self.create_context()
        current_payload = payload

        for ext in self._extensions:
            handlers = ext.handlers.get("before_provider_request", [])
            for handler in handlers:
                try:
                    event = BeforeProviderRequestEvent(payload=current_payload)
                    raw = await _call_handler(handler, event, ctx)
                    if raw is not None:
                        current_payload = raw
                except Exception as exc:
                    self.emit_error(ExtensionError(
                        extension_path=ext.path,
                        event="before_provider_request",
                        error=str(exc),
                        stack=_get_stack(exc),
                    ))

        return current_payload

    async def emit_before_agent_start(
        self,
        prompt: str,
        images: Optional[list[ImageContent]],
        system_prompt: str,
        system_prompt_options: Any,
    ) -> Optional[dict]:
        current_system_prompt = system_prompt

        # Build a context whose get_system_prompt tracks the evolving prompt
        class _DynamicCtx(RunnerExtensionContext):
            def get_system_prompt(self_inner) -> str:
                self._assert_active()
                return current_system_prompt

        ctx = _DynamicCtx(self)
        messages = []
        system_prompt_modified = False

        for ext in self._extensions:
            handlers = ext.handlers.get("before_agent_start", [])
            for handler in handlers:
                try:
                    event = BeforeAgentStartEvent(
                        prompt=prompt,
                        images=images,
                        system_prompt=current_system_prompt,
                        system_prompt_options=system_prompt_options,
                    )
                    raw = await _call_handler(handler, event, ctx)
                    result: Optional[BeforeAgentStartEventResult] = raw
                    if result:
                        if result.message:
                            messages.append(result.message)
                        if result.system_prompt is not None:
                            current_system_prompt = result.system_prompt
                            system_prompt_modified = True
                except Exception as exc:
                    self.emit_error(ExtensionError(
                        extension_path=ext.path,
                        event="before_agent_start",
                        error=str(exc),
                        stack=_get_stack(exc),
                    ))

        if messages or system_prompt_modified:
            return {
                "messages": messages if messages else None,
                "system_prompt": current_system_prompt if system_prompt_modified else None,
            }
        return None

    async def emit_resources_discover(
        self,
        cwd: str,
        reason: str,
    ) -> dict:
        ctx = self.create_context()
        skill_paths: list[dict] = []
        prompt_paths: list[dict] = []

        for ext in self._extensions:
            handlers = ext.handlers.get("resources_discover", [])
            for handler in handlers:
                try:
                    event = ResourcesDiscoverEvent(cwd=cwd, reason=reason)
                    raw = await _call_handler(handler, event, ctx)
                    result: Optional[ResourcesDiscoverResult] = raw
                    if result:
                        for p in (result.skill_paths or []):
                            skill_paths.append({"path": p, "extension_path": ext.path})
                        for p in (result.prompt_paths or []):
                            prompt_paths.append({"path": p, "extension_path": ext.path})
                except Exception as exc:
                    self.emit_error(ExtensionError(
                        extension_path=ext.path,
                        event="resources_discover",
                        error=str(exc),
                        stack=_get_stack(exc),
                    ))

        return {"skill_paths": skill_paths, "prompt_paths": prompt_paths}

    async def emit_input(
        self,
        text: str,
        images: Optional[list[ImageContent]],
        source: str,
    ) -> InputEventResult:
        ctx = self.create_context()
        current_text = text
        current_images = images

        for ext in self._extensions:
            for handler in ext.handlers.get("input", []):
                try:
                    event = InputEvent(text=current_text, images=current_images, source=source)
                    raw = await _call_handler(handler, event, ctx)
                    if raw is None:
                        continue
                    action = getattr(raw, "action", None) or (raw.get("action") if isinstance(raw, dict) else None)
                    if action == "handled":
                        return InputEventHandledResult()
                    if action == "transform":
                        current_text = getattr(raw, "text", current_text) or (raw.get("text", current_text) if isinstance(raw, dict) else current_text)
                        new_images = getattr(raw, "images", None) or (raw.get("images") if isinstance(raw, dict) else None)
                        if new_images is not None:
                            current_images = new_images
                except Exception as exc:
                    self.emit_error(ExtensionError(
                        extension_path=ext.path,
                        event="input",
                        error=str(exc),
                        stack=_get_stack(exc),
                    ))

        if current_text != text or current_images is not images:
            return InputEventTransformResult(text=current_text, images=current_images)
        return InputEventContinueResult()


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------

async def emit_session_shutdown_event(
    runner: ExtensionRunner,
    event: SessionShutdownEvent,
) -> bool:
    if runner.has_handlers("session_shutdown"):
        await runner.emit(event)
        return True
    return False


# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------

import asyncio
import inspect
import traceback


async def _call_handler(handler: Callable, event: Any, ctx: Any) -> Any:
    result = handler(event, ctx)
    if inspect.isawaitable(result):
        return await result
    return result


def _get_stack(exc: Exception) -> Optional[str]:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def _shallow_copy_event(event: Any) -> Any:
    import dataclasses
    if dataclasses.is_dataclass(event):
        return dataclasses.replace(event)
    return event


async def _async_noop(*args, **kwargs) -> None:
    pass


async def _cancelled_false(*args, **kwargs) -> dict:
    return {"cancelled": False}

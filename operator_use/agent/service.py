from __future__ import annotations

import asyncio
import inspect
import traceback
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from operator_use.agent.types import AgentConfig, AgentContext, PromptOptions, RetryStartEvent, RetryEndEvent
from operator_use.extension.types import (
    ExtensionContext, ExtensionError, ExtensionTool, ContextUsage, CompactOptions,
    InputEvent, BeforeAgentStartEvent, BeforeAgentStartEventResult,
    SessionBeforeCompactEvent, SessionBeforeCompactResult, SessionCompactEvent,
    AgentEndEvent,
    ToolCallEvent, ToolCallEventResult, ToolResultEvent, ToolResultEventResult,
    ContextEvent, ContextEventResult,
    SavePointEvent, SettledEvent, MessageEndEvent,
)
from operator_use.message.types import AssistantMessage, UserMessage, TextContent, ImageContent, Role, ToolResultContent, LLMMessage
from operator_use.inference.types import StopReason
from operator_use.message.utils import strip_unusable_trailing_assistant, filter_empty_assistant_messages
from operator_use.tool.types import ToolInvocation, ToolResult

from operator_use.prompt.builder import PromptTemplate
from operator_use.compaction.strategy.utils import estimate_context_tokens, estimate_tokens
from operator_use.agent.utils import is_permanent_error
from operator_use.agent.goals import GoalManager, judge_goal_with_llm
from operator_use.skill.review import SkillReviewTracker, spawn_skill_review
from operator_use.memory.review import MemoryReviewTracker, spawn_memory_review

if TYPE_CHECKING:
    from operator_use.engine.service import Engine
    from operator_use.session.manager import SessionManager
    from operator_use.resource.types import BaseResourceLoader
    from operator_use.extension.runtime import ExtensionRuntime
    from operator_use.compaction.strategy.base import Compaction
    from operator_use.runtime.service import Runtime
    from operator_use.memory.manager import MemoryManager
    from operator_use.agent.profile import AgentProfile




class Agent(ExtensionContext):
    """
    High-level agent session tying together Engine, SessionManager,
    ExtensionRuntime, ResourceLoader, and Compaction.

    Call `invoke()` to run a user turn. The session persists each message,
    tracks token usage, retries on transient errors, and triggers compaction
    when the context budget is exceeded.
    """

    def __init__(
        self,
        engine: Engine,
        session_manager: SessionManager,
        resource_loader: BaseResourceLoader,
        extension_runtime: ExtensionRuntime,
        compaction: Compaction,
        config: AgentConfig,
        memory_manager: MemoryManager | None = None,
    ) -> None:
        """Wire together all subsystems and register engine-level callbacks."""
        self._engine = engine
        self._session_manager = session_manager
        self._resources = resource_loader
        self._extensions = extension_runtime
        self._compaction = compaction
        self._config = config
        self._memory_manager = memory_manager
        self._system_prompt: str = ""
        self._system_prompt_cache: dict[str | None, str] = {}
        self._context_tokens: int = 0
        self._context_window: int = config.context_window
        self._compact_requested: bool = False
        self._compact_options: CompactOptions | None = None
        self._runtime: Runtime | None = None
        self._skill_review = SkillReviewTracker()
        self._memory_review = MemoryReviewTracker()
        def _make_judge_llm():
            """Build an LLM instance for goal judging, falling back to the main engine LLM if no auxiliary task is configured."""
            try:
                from operator_use.settings.manager import SettingsManager
                _sm = SettingsManager.get_instance()
                if _sm is None:
                    return engine.llm
                _aux = _sm.get_auxiliary_task("goal_judge")
                if _aux.model or _aux.provider:
                    from operator_use.inference.api.text.service import LLM
                    return LLM(model_id=_aux.model or engine.llm.model.id, provider=_aux.provider)
            except Exception:
                pass
            return engine.llm

        _judge_llm = _make_judge_llm()
        self._goal_manager = GoalManager(
            session_manager,
            judge=lambda goal, response, subgoals=None: judge_goal_with_llm(_judge_llm, goal, response, subgoals),
        )

        self._phase: str = "idle"
        self._rebooting: bool = False
        self._last_assistant_entry_id: str | None = None
        self._active_profile: AgentProfile | None = None
        self._baseline_llm = engine.llm
        self._engine.options.before_tool_call = self._before_tool_call
        self._engine.options.after_tool_call = self._after_tool_call
        self._engine.options.on_event = self._on_engine_event
        self._engine.options.get_ephemeral_messages = self._get_ephemeral_messages

    # -------------------------------------------------------------------------
    # Hooks
    # -------------------------------------------------------------------------

    @property
    def hooks(self):
        """Expose the extension runtime's hook registry for direct hook registration."""
        return self._extensions._hooks

    # -------------------------------------------------------------------------
    # ExtensionContext interface
    # -------------------------------------------------------------------------

    @property
    def cwd(self) -> Path:
        """Working directory for tool execution, sourced from AgentConfig."""
        return self._config.cwd

    @property
    def session_manager(self) -> Any:
        """Underlying session manager; exposed so extensions can query history."""
        return self._session_manager

    @property
    def model(self) -> Any | None:
        """Configured model identifier, or None when using the provider default."""
        return self._config.model

    @property
    def model_registry(self) -> Any:
        """Live model registry from the inference layer."""
        return self._engine.llm._models

    @property
    def signal(self) -> Any:
        """Abort signal that extensions can observe or trigger."""
        return self._engine._signal

    @property
    def _last_assistant_text(self) -> str:
        """Return concatenated text content of the most-recent assistant message, empty string if none."""
        for message in reversed(self._engine.state.messages):
            if message.role == Role.ASSISTANT:
                parts = [
                    c.content for c in getattr(message, 'contents', [])
                    if hasattr(c, 'content') and isinstance(c.content, str)
                ]
                return " ".join(parts)
        return ""

    def is_idle(self) -> bool:
        """True when no engine streaming is in progress."""
        return self._engine.is_idle

    def has_pending_messages(self) -> bool:
        """True when at least one message is queued in the engine for the next turn."""
        return self._engine.has_pending_messages()

    def abort(self) -> None:
        """Signal the engine to cancel the current streaming turn."""
        self._engine.abort()

    def shutdown(self) -> None:
        """Alias for abort; used by callers that manage agent lifecycle."""
        self._engine.abort()

    async def steer(self, text: str) -> None:
        """Inject a mid-turn steering message into the engine's steering queue."""
        msg = UserMessage.text(text)
        await self._engine.steer(msg)

    def get_context_usage(self) -> ContextUsage | None:
        """Return current token usage relative to the configured context window, or None if not yet known."""
        if not self._context_tokens:
            return None
        percent = (self._context_tokens / self._context_window * 100) if self._context_window else None
        return ContextUsage(
            tokens=self._context_tokens,
            context_window=self._context_window,
            percent=percent,
        )

    def get_system_prompt(self) -> str:
        """Return the cached system prompt from the most recent turn."""
        return self._system_prompt

    @property
    def goal_manager(self) -> GoalManager:
        """Goal lifecycle manager; extensions use this to set or inspect the active goal."""
        return self._goal_manager

    def compact(self, options: CompactOptions | None = None) -> None:
        """Request compaction after the current (or next) turn completes.

        Args:
            options: Optional CompactOptions to customize compaction behavior.
        """
        self._compact_requested = True
        self._compact_options = options

    async def run_compaction(self, custom_instructions: str | None = None) -> bool:
        """Run compaction immediately and emit save_point/settled events.

        Args:
            custom_instructions: Optional custom instructions for the compaction strategy.

        Returns:
            True if compaction was performed, False if no compaction was needed.

        Raises:
            RuntimeError: If the agent is currently busy (not idle).
        """
        if self._phase != "idle":
            raise RuntimeError(f"Agent is busy (phase={self._phase!r}). Wait for the current operation to finish.")

        performed = await self._run_compaction(custom_instructions)
        if not performed:
            return False

        await self._extensions.emit('save_point', SavePointEvent())
        if not self._engine.has_pending_messages():
            await self._extensions.emit('settled', SettledEvent())
        return True

    async def reload(self) -> None:
        """Reload all resources (tools, skills, prompts) and invalidate the system-prompt cache."""
        await self._resources.reload()
        self._system_prompt_cache.clear()

    async def wait_for_idle(self) -> None:
        """Block until the engine finishes any in-progress streaming turn."""
        await self._engine.wait_for_idle()

    async def new_session(self) -> None:
        """Delegate session creation to the runtime (no-op when no runtime is attached)."""
        if self._runtime is not None:
            await self._runtime.new_session()

    async def fork(self, entry_id: str) -> None:
        """Delegate session forking from a specific history entry to the runtime.

        Args:
            entry_id: The session entry ID to fork from.
        """
        if self._runtime is not None:
            await self._runtime.fork_session(entry_id)

    async def switch_session(self, session_file: Path) -> None:
        """Delegate session switching to the runtime.

        Args:
            session_file: Path to the session file to switch to.
        """
        if self._runtime is not None:
            await self._runtime.resume_session(session_file)

    # -------------------------------------------------------------------------
    # Ephemeral context injection
    # -------------------------------------------------------------------------

    async def _get_ephemeral_messages(self) -> list[LLMMessage]:
        """Build ephemeral messages injected into the LLM context at each turn start.

        These are never persisted to session history — the engine appends them
        to ctx_messages only for the current LLM call.

        use_screenshot / use_accessibility are baked into the desktop/browser
        instances at construction time. get_state(as_bytes=True) returns what
        those flags allow; None screenshot means use_screenshot=False.
        """
        msgs: list[LLMMessage] = []
        ctx = self._engine.tool_context

        # ── Desktop ──────────────────────────────────────────────────────────
        try:
            desktop = ctx.desktop
            if desktop is not None and desktop.is_open:
                state = desktop.get_state(as_bytes=True)
                content = f"[Desktop state]\n{state.to_string()}"
                if state.screenshot:
                    msgs.append(UserMessage.with_images(images=[state.screenshot], content=content))
                else:
                    msgs.append(UserMessage.text(content))
        except Exception:
            pass

        # ── Browser ──────────────────────────────────────────────────────────
        try:
            browser = ctx.browser
            if browser is not None and browser._client is not None:
                state = await browser.get_state(as_bytes=True)
                content = f"[Browser state]\n{state.to_string()}"
                if state.screenshot:
                    msgs.append(UserMessage.with_images(images=[state.screenshot], content=content))
                else:
                    msgs.append(UserMessage.text(content))
        except Exception:
            pass

        return msgs

    # -------------------------------------------------------------------------
    # Engine event fan-out (agent is the single funnel)
    # -------------------------------------------------------------------------

    async def _on_engine_event(self, event: Any) -> None:
        """Forward every engine-emitted event to extension handlers registered via api.on()."""
        event_type = getattr(event, 'type', None)
        if event_type is None:
            return
        for ext in self._extensions._extensions:
            for handler in ext.handlers.get(event_type, []):
                try:
                    result = handler(event, self)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    self._extensions._errors.append(ExtensionError(
                        extension_path=ext.path,
                        event=event_type,
                        error=traceback.format_exc().strip().splitlines()[-1],
                        stack=traceback.format_exc(),
                    ))

    # -------------------------------------------------------------------------
    # Engine-level tool hooks
    # -------------------------------------------------------------------------

    async def _before_tool_call(
        self,
        invocation: ToolInvocation,
        signal: object,
    ) -> ToolInvocation | ToolResultContent | None:
        """Fan out the tool_call event; returning ToolResultContent short-circuits execution."""
        if invocation.name != 'skill':
            self._skill_review.on_tool_call()
        if invocation.name != 'memory':
            self._memory_review.on_tool_call()

        results = await self._extensions.emit(
            'tool_call',
            ToolCallEvent(
                tool_call_id=invocation.id,
                tool_name=invocation.name,
                input=invocation.params,
            ),
        )
        for r in results:
            if isinstance(r, ToolCallEventResult):
                if r.block:
                    return ToolResultContent(
                        id=invocation.id,
                        is_error=True,
                        content=r.reason or 'Tool call blocked by extension.',
                        metadata={},
                    )
                if r.params is not None:
                    invocation = ToolInvocation(
                        id=invocation.id,
                        name=invocation.name,
                        params=r.params,
                        cwd=invocation.cwd,
                    )
        return invocation

    async def _after_tool_call(
        self,
        invocation: ToolInvocation,
        result: ToolResult,
        signal: object,
    ) -> ToolResult | None:
        """Fan out the tool_result event; extensions may override content, error flag, or terminate."""
        results = await self._extensions.emit(
            'tool_result',
            ToolResultEvent(
                tool_call_id=result.id,
                tool_name=invocation.name,
                input=invocation.params,
                content=result.content,
                is_error=result.is_error,
            ),
        )
        modified = result
        for r in results:
            if isinstance(r, ToolResultEventResult):
                if r.content is not None or r.is_error is not None or r.terminate:
                    modified = ToolResult(
                        id=result.id,
                        content=r.content if r.content is not None else result.content,
                        is_error=r.is_error if r.is_error is not None else result.is_error,
                        metadata=result.metadata,
                        terminate=r.terminate or result.terminate,
                    )
        return modified

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Agent profile management
    # -------------------------------------------------------------------------

    async def apply_profile(self, profile: AgentProfile) -> None:
        """Switch to a named agent profile: reload resources from profile dirs, update LLM.

        Args:
            profile: The AgentProfile to apply.
        """
        self._active_profile = profile
        self._resources.set_active_profile(profile)
        await self._resources.reload()
        self._sync_tools_from_resources(profile.tools)
        if profile.model_id:
            from operator_use.inference.api.text.service import LLM
            self._engine.llm = LLM(
                model_id=profile.model_id,
                provider=profile.provider,
                auth_store=self._engine.llm._auth_store,
            )

    async def clear_profile(self) -> None:
        """Remove the active profile and restore global resources and baseline LLM."""
        self._active_profile = None
        self._resources.set_active_profile(None)
        await self._resources.reload()
        self._sync_tools_from_resources([])
        self._engine.llm = self._baseline_llm

    def get_active_profile(self) -> AgentProfile | None:
        """Return the currently-active named agent profile, or None if running under the global config."""
        return self._active_profile

    def _sync_tools_from_resources(self, allowlist: list[str]) -> None:
        """Update the engine's tool set from the freshly-loaded resource loader.

        If allowlist is non-empty, only builtin tools whose names are in the
        list are kept; profile-local tools (from the profile's tools/ dir) are
        always included regardless.
        """
        all_tools = self._resources.get_tools()
        if allowlist:
            allowed = set(allowlist)
            filtered = [t for t in all_tools if t.name in allowed]
        else:
            filtered = all_tools
        self._engine.tools = filtered
        self._engine._tools = {t.name: t for t in filtered}
        self._engine.state.tools = list(filtered)

    def _rebuild_system_prompt(self, channel: str | None = None) -> str:
        """Assemble the full system prompt from SOUL.md, AGENT.md, SYSTEM.md, and appended parts."""
        skills, _ = self._resources.get_skills()
        # SYSTEM.md fully overrides identity; the AGENT.md body is the operation
        # manual, which sits alongside SOUL.md rather than replacing it.
        custom_prompt = self._resources.get_system_prompt()
        operation_manual = (
            self._active_profile.system_prompt
            if self._active_profile and self._active_profile.system_prompt
            else None
        )
        append_parts = self._resources.get_append_system_prompt()
        append_system_prompt = "\n\n".join(append_parts) if append_parts else None

        return PromptTemplate(
            cwd=str(self._config.cwd),
            custom_prompt=custom_prompt,
            operation_manual=operation_manual,
            tools=self._engine.state.tools,
            prompt_guidelines=self._config.prompt_guidelines,
            append_system_prompt=append_system_prompt,
            skills=skills,
            soul_prompt=self._resources.get_soul_prompt(),
            user_profile=self._resources.get_user_profile(),
            agent_memory=self._resources.get_agent_memory(),
            tools_reference=self._resources.get_tools_reference(),
            channel=channel,
            session_id=self._session_manager.session_id,
            profile_dir=self._active_profile.profile_dir if self._active_profile else None,
        ).build()

    def _register_message_handler(self, persisted_ids: list[str], error_holder: list[AssistantMessage]) -> Callable:
        """Register a message_end hook that persists messages and tracks token usage."""
        async def _on_message_end(event: MessageEndEvent) -> None:
            """Persist completed messages to session and update token usage; hold back error messages until final failure."""
            message = event.message
            if message is None:
                return
            if message.role == Role.ASSISTANT:
                assert isinstance(message, AssistantMessage)
                total = message.usage.input_tokens + message.usage.output_tokens
                if total:
                    self._context_tokens = total
                # Error messages are held back — only written to session on
                # final failure so intermediate retry errors don't pollute the log.
                # Abort is handled by the engine (synthetic closing message, stop_reason=Stop)
                # so it never reaches this guard.
                if message.stop_reason == StopReason.Error:
                    error_holder.clear()
                    error_holder.append(message)
                    return
                entry_id = self._session_manager.append_message(message)
                persisted_ids.append(entry_id)
                self._last_assistant_entry_id = entry_id
            elif message.role == Role.TOOL:
                entry_id = self._session_manager.append_message(message)
                persisted_ids.append(entry_id)

        return self.hooks.register('message_end', _on_message_end)

    def _refresh_context_tokens_from_session(self) -> None:
        """Re-estimate context size from persisted session state after compaction.

        Do not trust historical assistant usage here: retained assistant messages
        may still carry pre-compaction provider usage from the old, larger
        prompt. Use the message-size heuristic for the rebuilt compacted context.
        """
        session_ctx = self._session_manager.build_session_context()
        self._context_tokens = sum(estimate_tokens(message) for message in session_ctx.messages)

    def _rewind_session(self, persisted_ids: list[str]) -> None:
        """Remove session entries appended during a failed attempt."""
        if not persisted_ids:
            return
        first_entry = self._session_manager.by_id.get(persisted_ids[0])
        parent_of_first = first_entry.parent_id if first_entry else None
        for entry_id in persisted_ids:
            entry = self._session_manager.by_id.pop(entry_id, None)
            if entry and entry in self._session_manager.entries:
                self._session_manager.entries.remove(entry)
        self._session_manager.leaf_id = parent_of_first
        persisted_ids.clear()

    def _maybe_spawn_skill_review(self) -> None:
        """Spawn a background thread to review the conversation and update skills."""
        tools_by_name = {t.name: t for t in self._engine.state.tools}
        skill_tool = tools_by_name.get('skill')
        if skill_tool is None:
            return
        messages = list(self._engine.state.messages)
        if not messages:
            return
        spawn_skill_review(
            llm=self._engine.llm,
            messages=messages,
            skill_manage_tool=skill_tool,
            skill_view_tool=skill_tool,
        )

    def _maybe_spawn_memory_review(self) -> None:
        """Spawn a background thread to review the conversation and save memory facts."""
        if self._memory_manager is None or self._memory_manager.api is None:
            return
        tools_by_name = {t.name: t for t in self._engine.state.tools}
        memory_tool = tools_by_name.get('memory')
        if memory_tool is None:
            return
        messages = list(self._engine.state.messages)
        if not messages:
            return
        spawn_memory_review(
            llm=self._engine.llm,
            messages=messages,
            memory_tool=memory_tool,
            memory_manager=self._memory_manager,
            on_complete=self._system_prompt_cache.clear,
        )

    def _active_todo_injection(self) -> str | None:
        """Return a formatted todo list for prompt injection if the todo tool is loaded and has items."""
        for tool in self._engine.state.tools:
            formatter = getattr(tool, 'format_for_injection', None)
            if tool.name == 'todo' and callable(formatter):
                injection = formatter()
                if injection:
                    return str(injection)
        return None

    def _hydrate_todo_store(self, messages: list[Any]) -> None:
        """Replay message history into the todo tool's in-memory store so it reflects persisted state."""
        for tool in self._engine.state.tools:
            hydrator = getattr(tool, 'hydrate_from_messages', None)
            if tool.name == 'todo' and callable(hydrator):
                hydrator(messages)

    # -------------------------------------------------------------------------
    # Core turn entry point
    # -------------------------------------------------------------------------

    async def invoke(self, user_input: str, options: PromptOptions | None = None) -> None:
        """Run one user turn with retry on transient errors.

        Persists the user message to session history, reconstructs context with
        memory recall and current time, then streams the LLM and executes tools.
        Failed turns are rewound before retry; compaction runs at save points.

        Args:
            user_input: The user's text input for this turn.
            options: Optional PromptOptions for metadata, channel, or behavior overrides.

        Raises:
            RuntimeError: If the agent is already busy or is rebooting.
        """
        if self._phase != "idle":
            if self._rebooting:
                raise RuntimeError("Rebooting — please send your message again in a moment.")
            raise RuntimeError(f"Agent is busy (phase={self._phase!r}). Wait for the current operation to finish.")

        opts = options or PromptOptions()

        # Notify extensions of incoming input
        await self._extensions.emit('input', InputEvent(text=user_input, source=opts.source))

        # Start memory prefetch as a background task so the sync prompt rebuild
        # runs while the SQLite / vector search is in flight.
        async def _prefetch() -> str:
            """Retrieve recalled memory context relevant to the incoming user input."""
            if self._memory_manager:
                return await self._memory_manager.prefetch(
                    user_input, session_id=self._session_manager.session_id or ""
                )
            return ""

        prefetch_task = asyncio.ensure_future(_prefetch())

        # System prompt is cached per channel; only rebuilt after a resource
        # reload or when the memory review background thread updates MEMORY.md.
        cached_prompt = self._system_prompt_cache.get(opts.channel)
        if cached_prompt is not None:
            self._system_prompt = cached_prompt
        else:
            self._system_prompt = self._rebuild_system_prompt(channel=opts.channel)
            self._system_prompt_cache[opts.channel] = self._system_prompt

        memory_context = await prefetch_task

        before_results = await self._extensions.emit(
            'before_agent_start',
            BeforeAgentStartEvent(prompt=user_input, system_prompt=self._system_prompt),
        )
        for result in before_results:
            if isinstance(result, BeforeAgentStartEventResult) and result.system_prompt:
                self._system_prompt = result.system_prompt

        # Reconstruct message history from persisted session
        session_ctx = self._session_manager.build_session_context()
        base_messages = list(session_ctx.messages)
        self._hydrate_todo_store(base_messages)

        # context hook — extensions can replace the messages sent to the LLM
        context_results = await self._extensions.emit(
            'context',
            ContextEvent(messages=base_messages),
        )
        for r in context_results:
            if isinstance(r, ContextEventResult) and r.messages is not None:
                base_messages = r.messages

        # The session is append-only: a failed/interrupted turn leaves its
        # record intact (an empty error assistant message and/or an assistant
        # message whose tool_calls never got results). Those are kept on disk
        # for the audit trail but must not be sent to the provider, which
        # rejects dangling tool_calls / empty assistant turns. Strip them from
        # the *context* only (mirrors the engine's run_continue() guard).
        base_messages = strip_unusable_trailing_assistant(base_messages)
        base_messages = filter_empty_assistant_messages(base_messages)

        # Persist the user message once (not retried) — clean, no memory embedded
        user_message = UserMessage(contents=[TextContent(content=user_input)])
        user_entry_id = self._session_manager.append_message(user_message, meta=opts.meta)

        # Build a context-only user message with recalled memory, current datetime,
        # and any injected hints. This is never persisted — session history stays clean.
        now = datetime.now().astimezone()
        time_str = now.strftime("%A, %-d %B %Y at %-I:%M %p %Z")
        prefix = f"<memory>\n{memory_context}\n</memory>\n\n" if memory_context else ""
        prefix += f"Current time: {time_str}\n\n"

        # Runtime context — model, TTS/STT state
        try:
            from operator_use.settings.manager import SettingsManager
            _sm = SettingsManager.get_instance()
            if _sm and self._active_profile:
                # Apply profile-specific settings overlay
                _effective_settings = _sm.settings_with_profile_overlay(self._active_profile.settings_path)
                _tts_enabled = _effective_settings.tts.enabled if (_effective_settings and _effective_settings.tts) else None
                _stt_enabled = _effective_settings.stt.enabled if (_effective_settings and _effective_settings.stt) else None
            else:
                # Fall back to global settings if no profile is active
                _tts = _sm.get_tts_settings() if _sm else None
                _stt = _sm.get_stt_settings() if _sm else None
                _tts_enabled = _tts.enabled if _tts else None
                _stt_enabled = _stt.enabled if _stt else None
        except Exception:
            _tts_enabled = None
            _stt_enabled = None

        _current_model = self._engine.llm.model.id
        _current_provider = self._engine.llm.model.provider
        prefix += f"Current model: {_current_model} ({_current_provider})\n"
        prefix += f"STT: {'enabled' if _stt_enabled else 'disabled'} | "
        prefix += f"TTS: {'enabled' if _tts_enabled else 'disabled'}\n\n"

        if _tts_enabled:
            prefix += (
                "Note: Text-to-speech is enabled. Respond in plain, natural language "
                "without markdown formatting (no **bold**, no bullet lists, no headers, "
                "no code fences) so the response sounds natural when spoken.\n\n"
            )

        ctx_contents: list = [TextContent(content=f"{prefix}{user_input}")]
        for img_path in opts.images:
            ctx_contents.append(ImageContent.from_file(img_path))
        user_message = UserMessage(contents=ctx_contents)

        # Assemble tools: base tools + extension tools (base names take priority)
        base_tool_names = {t.name for t in self._engine.tools}
        ext_tools = [
            ExtensionTool(rt.definition, self)
            for name, rt in self._extensions.get_tools().items()
            if name not in base_tool_names
        ]

        # Build the context snapshot that the loop will receive
        ctx = AgentContext(
            system_prompt=self._system_prompt,
            messages=base_messages + [user_message],
            tools=list(self._engine.tools) + ext_tools,
        )

        self._phase = "turn"
        try:
            await self._run_with_retry(ctx, user_entry_id)
        finally:
            self._phase = "idle"

        # Persist the completed exchange to the memory provider
        if self._memory_manager:
            assistant_text = self._last_assistant_text
            await self._memory_manager.on_turn_complete(
                user_input, assistant_text,
                session_id=self._session_manager.session_id or "",
            )
            # Warm the cache for the next turn in the background so prefetch()
            # can return instantly instead of blocking on an external lookup.
            self._memory_manager.queue_prefetch(
                assistant_text,
                session_id=self._session_manager.session_id or "",
            )

        # Session writes are now flushed — notify observers
        await self._extensions.emit('save_point', SavePointEvent())

        # If a terminating tool scheduled a deferred action (e.g. reboot),
        # allow extensions to dispatch final messages to channels BEFORE termination
        deferred = self._engine._deferred_fn
        if deferred is not None:
            # Emit agent_end early so extensions can dispatch messages to channels
            # before the deferred action (reboot) terminates the process
            await self._extensions.emit(
                'agent_end',
                AgentEndEvent(messages=self._engine.state.messages),
            )
            # Now run the deferred action (this may call sys.exit)
            self._engine._deferred_fn = None
            self._rebooting = True
            self._phase = "turn"
            await deferred()
            return  # deferred action takes over (e.g. sys.exit); don't continue

        # Notify extensions the turn ended
        await self._extensions.emit(
            'agent_end',
            AgentEndEvent(messages=self._engine.state.messages),
        )

        # Trigger background skill review if threshold reached
        if self._skill_review.should_review():
            self._skill_review.reset()
            self._maybe_spawn_skill_review()

        # Trigger background memory review if threshold reached
        if self._memory_review.should_review():
            self._memory_review.reset()
            self._maybe_spawn_memory_review()

        # Trigger compaction if requested or context budget exceeded
        if self._compact_requested or self._compaction.should_compact(
            self._context_tokens, self._context_window
        ):
            await self._run_compaction(opts.compaction_custom_instructions)

        if await self._continue_goal_if_needed():
            return

        # Agent is done with no more queued turns
        if not self._engine.has_pending_messages():
            await self._extensions.emit('settled', SettledEvent())

    async def _continue_goal_if_needed(self) -> bool:
        """Evaluate the active goal after a turn and re-invoke the agent if continuation is needed."""
        if not self._goal_manager.is_active():
            return False

        decision = await self._goal_manager.evaluate_after_turn(self._last_assistant_text)
        continuation = decision.get("continuation_prompt")
        if decision.get("should_continue") and isinstance(continuation, str) and continuation.strip():
            await self.invoke(continuation, PromptOptions(source='goal'))
            return True
        return False

    async def _run_with_retry(self, ctx: AgentContext, user_entry_id: str) -> None:
        """Run the engine loop with exponential-backoff retry; rewinds session on transient failures."""
        max_retries = self._config.retry_max_retries if self._config.retry_enabled else 0
        base_delay_s = self._config.retry_base_delay_ms / 1000

        persisted_ids: list[str] = []
        error_holder: list[AssistantMessage] = []

        for attempt in range(max_retries + 1):
            if attempt > 0:
                delay = base_delay_s * (2 ** (attempt - 1))
                await self._extensions.emit(
                    'retry_start',
                    RetryStartEvent(attempt=attempt, max_retries=max_retries),
                )
                await asyncio.sleep(delay)

            persisted_ids.clear()
            error_holder.clear()
            unsubscribe = self._register_message_handler(persisted_ids, error_holder)
            try:
                await self._engine.run(ctx)
            finally:
                unsubscribe()

            error = self._engine.state.error_message
            if error is None:
                # Success
                if attempt > 0:
                    await self._extensions.emit(
                        'retry_end',
                        RetryEndEvent(attempt=attempt, success=True),
                    )
                return

            self._engine.reset()

            # Permanent errors (bad/missing API key, bad request, model not
            # found) will fail identically on every retry — stop now instead
            # of burning the remaining attempts. Transient errors (rate limit,
            # overload, 5xx) are already auto-retried with the provider's
            # Retry-After by the underlying SDK; this loop is the outer cushion.
            permanent = is_permanent_error(error)

            if not permanent and attempt < max_retries:
                # Keep tool calls/results already persisted — the next attempt
                # rebuilds ctx from the session so the LLM sees the work already
                # done and continues from where it left off rather than replaying
                # from scratch. The error message itself is discarded (held in
                # error_holder but not written) so intermediate retry noise never
                # lands in the session file.
                session_ctx = self._session_manager.build_session_context()
                ctx = AgentContext(
                    system_prompt=ctx.system_prompt,
                    messages=filter_empty_assistant_messages(strip_unusable_trailing_assistant(session_ctx.messages)),
                    tools=ctx.tools,
                )
                await self._extensions.emit(
                    'retry_end',
                    RetryEndEvent(attempt=attempt, success=False, error=error),
                )
            else:
                # Permanent error, or retries exhausted — write the final error
                # assistant message to the session so the record is complete and
                # the user can see what went wrong. It is filtered from LLM
                # context at turn-build time by strip_unusable_trailing_assistant.
                if error_holder:
                    self._session_manager.append_message(error_holder[0])
                detail = (
                    "permanent error, not retried"
                    if permanent
                    else f"{attempt + 1} attempt(s)"
                )
                raise RuntimeError(
                    f"Agent failed ({detail}): {error}."
                )

    # -------------------------------------------------------------------------
    # Compaction
    # -------------------------------------------------------------------------

    async def _run_compaction(self, custom_instructions: str | None = None) -> bool:
        """Execute compaction: prepare, run strategy, append compaction entry, and emit events."""
        self._compact_requested = False
        compact_opts = self._compact_options
        self._compact_options = None
        self._phase = "compaction"
        try:
            path_entries = self._session_manager.get_branch()
            preparation = self._compaction.prepare(path_entries)
            if preparation is None:
                return False

            before_results = await self._extensions.emit(
                'session_before_compact',
                SessionBeforeCompactEvent(
                    preparation=preparation,
                    branch_entries=path_entries,
                    custom_instructions=custom_instructions,
                ),
            )

            compaction_result = None
            for r in before_results:
                if isinstance(r, SessionBeforeCompactResult):
                    if r.cancel:
                        return False
                    if r.compaction:
                        compaction_result = r.compaction

            ci = custom_instructions
            if compact_opts and compact_opts.custom_instructions:
                ci = compact_opts.custom_instructions

            if self._memory_manager:
                messages_raw = [m.model_dump() for m in path_entries]
                await self._memory_manager.on_pre_compact(messages_raw)

            if compaction_result is None:
                compaction_result = await self._compaction.compact(preparation, ci)

            self._hydrate_todo_store(self._session_manager.build_session_context().messages)
            if todo_injection := self._active_todo_injection():
                compaction_result.summary = f"{compaction_result.summary}\n\n{todo_injection}"

            self._session_manager.append_compaction(
                summary=compaction_result.summary,
                first_kept_entry_id=compaction_result.retained_from_id,
                tokens_before=compaction_result.tokens_before,
                details=compaction_result.details,
            )
            self._refresh_context_tokens_from_session()

            compact_entry = self._session_manager.get_leaf_entry()
            await self._extensions.emit(
                'session_compact',
                SessionCompactEvent(compaction_entry=compact_entry),
            )

            if compact_opts and compact_opts.on_complete:
                compact_opts.on_complete(compaction_result)
            return True
        finally:
            self._phase = "idle"

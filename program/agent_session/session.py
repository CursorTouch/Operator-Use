from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from program.agent_session.types import AgentSessionConfig, PromptOptions, RetryStartEvent, RetryEndEvent
from program.extension.types import (
    ExtensionContext, ContextUsage, CompactOptions,
    InputEvent, BeforeAgentStartEvent, BeforeAgentStartEventResult,
    SessionBeforeCompactEvent, SessionBeforeCompactResult, SessionCompactEvent,
    AgentEndEvent as ExtAgentEndEvent,
    ToolCallEvent, ToolCallEventResult, ToolResultEvent, ToolResultEventResult,
    ContextEvent, ContextEventResult,
    SavePointEvent, SettledEvent,
)
from program.message.types import AssistantMessage, UserMessage, TextContent, Role, ToolResultContent
from program.tool.types import ToolInvocation, ToolResult

from program.prompts.builder import build_system_prompt
from program.prompts.types import BuildSystemPromptOptions

if TYPE_CHECKING:
    from program.engine.loop import AgentLoop
    from program.session.manager import SessionManager
    from program.resource.types import BaseResourceLoader
    from program.extension.runtime import ExtensionRuntime
    from program.compaction.compact import Compaction
    from program.engine.types import AgentEvent


class AgentSession(ExtensionContext):
    """
    High-level agent session tying together AgentLoop, SessionManager,
    ExtensionRuntime, ResourceLoader, and Compaction.

    Call `prompt()` to run a user turn. The session persists each message,
    tracks token usage, retries on transient errors, and triggers compaction
    when the context budget is exceeded.
    """

    def __init__(
        self,
        loop: AgentLoop,
        session_manager: SessionManager,
        resource_loader: BaseResourceLoader,
        extension_runtime: ExtensionRuntime,
        compaction: Compaction,
        config: AgentSessionConfig,
    ) -> None:
        self._loop = loop
        self._session = session_manager
        self._resources = resource_loader
        self._extensions = extension_runtime
        self._compaction = compaction
        self._config = config
        self._system_prompt: str = ""
        self._context_tokens: int = 0
        self._context_window: int = config.context_window
        self._compact_requested: bool = False
        self._compact_options: CompactOptions | None = None

        self._phase: str = "idle"
        self._loop.options.before_tool_call = self._before_tool_call
        self._loop.options.after_tool_call = self._after_tool_call

    # -------------------------------------------------------------------------
    # ExtensionContext interface
    # -------------------------------------------------------------------------

    @property
    def cwd(self) -> Path:
        return self._config.cwd

    @property
    def session_manager(self) -> Any:
        return self._session

    @property
    def model(self) -> Any | None:
        return self._config.model

    def is_idle(self) -> bool:
        return self._loop.is_idle

    def has_pending_messages(self) -> bool:
        return self._loop.has_pending_messages()

    def abort(self) -> None:
        self._loop.abort()

    def shutdown(self) -> None:
        self._loop.abort()

    def get_context_usage(self) -> ContextUsage | None:
        if not self._context_tokens:
            return None
        percent = (self._context_tokens / self._context_window * 100) if self._context_window else None
        return ContextUsage(
            tokens=self._context_tokens,
            context_window=self._context_window,
            percent=percent,
        )

    def get_system_prompt(self) -> str:
        return self._system_prompt

    def compact(self, options: CompactOptions | None = None) -> None:
        """Request compaction after the current (or next) turn completes."""
        self._compact_requested = True
        self._compact_options = options

    # -------------------------------------------------------------------------
    # Engine-level tool hooks
    # -------------------------------------------------------------------------

    async def _before_tool_call(
        self,
        invocation: ToolInvocation,
        signal: object,
    ) -> ToolInvocation | ToolResultContent | None:
        results = await self._extensions.emit(
            'tool_call',
            ToolCallEvent(
                tool_call_id=invocation.id,
                tool_name=invocation.name,
                input=invocation.params,
            ),
        )
        for r in results:
            if isinstance(r, ToolCallEventResult) and r.block:
                return ToolResultContent(
                    id=invocation.id,
                    is_error=True,
                    content=r.reason or 'Tool call blocked by extension.',
                    metadata={},
                )
        return invocation

    async def _after_tool_call(
        self,
        invocation: ToolInvocation,
        result: ToolResult,
        signal: object,
    ) -> ToolResult | None:
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

    def _rebuild_system_prompt(self) -> str:
        skills, _ = self._resources.get_skills()
        context_files = self._resources.get_context_files()
        custom_prompt = self._resources.get_system_prompt()
        append_parts = self._resources.get_append_system_prompt()
        append_system_prompt = "\n\n".join(append_parts) if append_parts else None

        options = BuildSystemPromptOptions(
            cwd=str(self._config.cwd),
            custom_prompt=custom_prompt,
            selected_tools=self._config.selected_tools,
            tool_snippets=self._config.tool_snippets,
            prompt_guidelines=self._config.prompt_guidelines,
            append_system_prompt=append_system_prompt,
            context_files=context_files,
            skills=skills,
        )
        return build_system_prompt(options)

    def _make_event_handler(self, persisted_ids: list[str]):
        """Return a loop event listener that persists messages and tracks token usage."""
        async def _on_event(event: AgentEvent) -> None:
            from program.engine.types import MessageEndEvent as EngineMessageEndEvent
            match event:
                case EngineMessageEndEvent(message=message):
                    if message.role == Role.ASSISTANT:
                        assert isinstance(message, AssistantMessage)
                        # Update live context token count from the model's reported usage
                        total = message.usage.input_tokens + message.usage.output_tokens
                        if total:
                            self._context_tokens = total
                        entry_id = self._session.append_message(message)
                        persisted_ids.append(entry_id)
                    elif message.role == Role.TOOL:
                        entry_id = self._session.append_message(message)
                        persisted_ids.append(entry_id)
        return _on_event

    def _rewind_session(self, persisted_ids: list[str]) -> None:
        """Remove session entries appended during a failed attempt."""
        for entry_id in persisted_ids:
            entry = self._session.by_id.pop(entry_id, None)
            if entry and entry in self._session.entries:
                self._session.entries.remove(entry)
            # Restore leaf to the entry before the first one we added
        if persisted_ids:
            parent_of_first = None
            first = self._session.by_id.get(persisted_ids[0])
            if first:
                parent_of_first = first.parent_id
            self._session.leaf_id = parent_of_first
        persisted_ids.clear()

    # -------------------------------------------------------------------------
    # Core turn entry point
    # -------------------------------------------------------------------------

    async def prompt(self, user_input: str, options: PromptOptions | None = None) -> None:
        """Run one user turn with retry on transient errors."""
        if self._phase != "idle":
            raise RuntimeError(f"Agent is busy (phase={self._phase!r}). Wait for the current operation to finish.")

        opts = options or PromptOptions()

        # Notify extensions of incoming input
        await self._extensions.emit('input', InputEvent(text=user_input, source=opts.source))

        # Build system prompt and allow extensions to override it
        self._system_prompt = self._rebuild_system_prompt()
        before_results = await self._extensions.emit(
            'before_agent_start',
            BeforeAgentStartEvent(prompt=user_input, system_prompt=self._system_prompt),
        )
        for result in before_results:
            if isinstance(result, BeforeAgentStartEventResult) and result.system_prompt:
                self._system_prompt = result.system_prompt

        self._loop.state.system_prompt = self._system_prompt

        # Reconstruct message history from persisted session
        session_ctx = self._session.build_session_context()
        base_messages = list(session_ctx.messages)

        # context hook — extensions can replace the messages sent to the LLM
        context_results = await self._extensions.emit(
            'context',
            ContextEvent(messages=base_messages),
        )
        for r in context_results:
            if isinstance(r, ContextEventResult) and r.messages is not None:
                base_messages = r.messages

        # Persist the user message once (not retried)
        user_message = UserMessage(contents=[TextContent(content=user_input)])
        user_entry_id = self._session.append_message(user_message)

        self._phase = "turn"
        try:
            await self._run_with_retry(base_messages + [user_message], user_entry_id)
        finally:
            self._phase = "idle"

        # Session writes are now flushed — notify observers
        await self._extensions.emit('save_point', SavePointEvent())

        # Notify extensions the turn ended
        await self._extensions.emit(
            'agent_end',
            ExtAgentEndEvent(messages=self._loop.state.messages),
        )

        # Trigger compaction if requested or context budget exceeded
        if self._compact_requested or self._compaction.should_compact(
            self._context_tokens, self._context_window
        ):
            await self._run_compaction(opts.compaction_custom_instructions)

        # Agent is done with no more queued turns
        if not self._loop.has_pending_messages():
            await self._extensions.emit('settled', SettledEvent())

    async def _run_with_retry(self, messages: list, user_entry_id: str) -> None:
        max_retries = self._config.retry_max_retries if self._config.retry_enabled else 0
        base_delay_s = self._config.retry_base_delay_ms / 1000

        persisted_ids: list[str] = []

        for attempt in range(max_retries + 1):
            if attempt > 0:
                delay = base_delay_s * (2 ** (attempt - 1))
                await self._extensions.emit(
                    'retry_start',
                    RetryStartEvent(attempt=attempt, max_retries=max_retries),
                )
                await asyncio.sleep(delay)

            persisted_ids.clear()
            handler = self._make_event_handler(persisted_ids)
            unsubscribe = await self._loop.subscribe(handler)
            try:
                await self._loop.run(messages)
            finally:
                unsubscribe()

            error = self._loop.state.error_message
            if error is None:
                # Success
                if attempt > 0:
                    await self._extensions.emit(
                        'retry_end',
                        RetryEndEvent(attempt=attempt, success=True),
                    )
                return

            # Failed attempt — rewind session entries from this attempt
            self._rewind_session(persisted_ids)
            self._loop.reset()

            if attempt < max_retries:
                await self._extensions.emit(
                    'retry_end',
                    RetryEndEvent(attempt=attempt, success=False, error=error),
                )
            else:
                # All retries exhausted — rewind user message too and re-raise
                self._session.entries = [
                    e for e in self._session.entries if e.id != user_entry_id
                ]
                self._session.by_id.pop(user_entry_id, None)
                self._session.leaf_id = self._session.entries[-1].id if self._session.entries else None
                raise RuntimeError(f"Agent failed after {attempt + 1} attempt(s): {error}")

    # -------------------------------------------------------------------------
    # Compaction
    # -------------------------------------------------------------------------

    async def _run_compaction(self, custom_instructions: str | None = None) -> None:
        self._compact_requested = False
        compact_opts = self._compact_options
        self._compact_options = None
        self._phase = "compaction"

        path_entries = self._session.get_branch()
        preparation = self._compaction.prepare(path_entries)
        if preparation is None:
            return

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
                    return
                if r.compaction:
                    compaction_result = r.compaction

        ci = custom_instructions
        if compact_opts and compact_opts.custom_instructions:
            ci = compact_opts.custom_instructions

        if compaction_result is None:
            compaction_result = await self._compaction.compact(preparation, ci)

        self._session.append_compaction(
            summary=compaction_result.summary,
            first_kept_entry_id=compaction_result.retained_from_id,
            tokens_before=compaction_result.tokens_before,
            details=compaction_result.details,
        )

        compact_entry = self._session.get_leaf_entry()
        self._phase = "idle"
        await self._extensions.emit(
            'session_compact',
            SessionCompactEvent(compaction_entry=compact_entry),
        )

        if compact_opts and compact_opts.on_complete:
            compact_opts.on_complete(compaction_result)

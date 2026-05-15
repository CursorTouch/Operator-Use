from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from program.agent_session.types import AgentSessionConfig, PromptOptions
from program.extension.types import (
    ExtensionContext, ContextUsage, CompactOptions,
    InputEvent, BeforeAgentStartEvent, BeforeAgentStartEventResult,
    SessionBeforeCompactEvent, SessionBeforeCompactResult, SessionCompactEvent,
    AgentEndEvent as ExtAgentEndEvent,
)
from program.message.types import UserMessage, TextContent, Role
from program.prompts.builder import build_system_prompt
from program.prompts.types import BuildSystemPromptOptions

if TYPE_CHECKING:
    from program.engine.loop import AgentLoop
    from program.session.manager import SessionManager
    from program.resource.types import ResourceLoader
    from program.extension.runtime import ExtensionRuntime
    from program.compaction.compact import Compaction
    from program.llm.service import LLM
    from program.engine.types import AgentEvent


class AgentSession(ExtensionContext):
    """
    High-level agent session tying together AgentLoop, SessionManager,
    ExtensionRuntime, ResourceLoader, and Compaction.

    Call `prompt()` to run a user turn. The session persists each message
    to the session file and triggers compaction when the context grows too large.
    """

    def __init__(
        self,
        loop: AgentLoop,
        session_manager: SessionManager,
        resource_loader: ResourceLoader,
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
        self._context_window: int = 200_000
        self._compact_requested: bool = False
        self._compact_options: CompactOptions | None = None

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

    # -------------------------------------------------------------------------
    # Core turn entry point
    # -------------------------------------------------------------------------

    async def prompt(self, user_input: str, options: PromptOptions | None = None) -> None:
        """Run one user turn: build context, invoke the loop, persist messages."""
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
        messages = list(session_ctx.messages)

        # Append and persist the user message
        user_message = UserMessage(contents=[TextContent(content=user_input)])
        messages.append(user_message)
        self._session.append_message(user_message)

        # Subscribe to loop events to persist each assistant/tool message
        async def _on_event(event: AgentEvent) -> None:
            from program.engine.types import MessageEndEvent as EngineMessageEndEvent
            match event:
                case EngineMessageEndEvent(message=message):
                    if message.role in (Role.ASSISTANT, Role.TOOL):
                        self._session.append_message(message)

        unsubscribe = await self._loop.subscribe(_on_event)
        try:
            await self._loop.run(messages)
        finally:
            unsubscribe()

        # Notify extensions that the agent turn has ended
        await self._extensions.emit(
            'agent_end',
            ExtAgentEndEvent(messages=self._loop.state.messages),
        )

        # Trigger compaction if explicitly requested or context budget exceeded
        needs_compact = self._compact_requested or self._compaction.should_compact(
            self._context_tokens, self._context_window
        )
        if needs_compact:
            await self._run_compaction(opts.compaction_custom_instructions)

    # -------------------------------------------------------------------------
    # Compaction
    # -------------------------------------------------------------------------

    async def _run_compaction(self, custom_instructions: str | None = None) -> None:
        self._compact_requested = False
        compact_opts = self._compact_options
        self._compact_options = None

        path_entries = self._session.get_branch()
        preparation = self._compaction.prepare(path_entries)
        if preparation is None:
            return

        # Let extensions inspect and optionally cancel / pre-supply the result
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

        # Merge custom instructions from caller and compact() call
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

        # Notify extensions that compaction completed
        compact_entry = self._session.get_leaf_entry()
        await self._extensions.emit(
            'session_compact',
            SessionCompactEvent(compaction_entry=compact_entry),
        )

        if compact_opts and compact_opts.on_complete:
            compact_opts.on_complete(compaction_result)

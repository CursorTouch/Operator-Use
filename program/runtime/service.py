from __future__ import annotations

from pathlib import Path

from program.runtime.types import RuntimeConfig, RuntimeContext
from program.agent.service import Agent
from program.agent.types import PromptOptions
from program.bus.service import Bus
from program.cron.types import CronJob
from program.commands.registry import CommandRegistry
from program.subagent.manager import SubagentManager
from program.commands.types import parse_command
from program.extension.types import (
    SessionStartEvent, SessionShutdownEvent, SessionBeforeSwitchEvent,
    SessionBeforeSwitchResult, SessionBeforeForkEvent, SessionBeforeForkResult,
)
from program.tool.types import ToolContext


class Runtime:
    """
    Orchestrates the full session lifecycle: creation, switching, forking,
    and slash-command dispatch on top of Agent / RuntimeContext.

    Usage:
        runtime = await Runtime.create(config)
        await runtime.user_input("/compact shrink the history")
        await runtime.user_input("explain this code")
    """

    def __init__(
        self,
        context: RuntimeContext,
        config: RuntimeConfig,
        bus: Bus | None = None,
    ) -> None:
        self._context = context
        self._config = config
        self.bus = bus or Bus()
        self.commands = CommandRegistry(
            runtime=self,
            discovered=self._context.resource_loader.get_commands(),
        )
        self.commands.register_from_extensions(
            self._context.extension_runtime.get_commands()
        )
        # Give the agent a back-reference so ctx.new_session() / fork() / switch_session() work
        if context.agent is not None:
            context.agent._runtime = self

        # Wire cron callback and start the scheduler
        if context.cron is not None:
            context.cron.on_job = self._handle_cron_job
            context.cron.start()

        self.subagent_manager = self._create_subagent_manager(context)
        # Merge subagent profiles registered by extensions on top of file-discovered ones.
        ext_profiles = context.extension_runtime.get_subagent_profiles()
        if ext_profiles:
            merged = context.resource_loader.get_subagent_profiles() + ext_profiles
            self.subagent_manager.update_profiles(merged)
        # Expose MCPManager for use in create_session_agent() and shutdown.
        self.mcp_manager = context.mcp_manager
        self._configure_context(context)

    def _create_subagent_manager(self, context: RuntimeContext) -> SubagentManager:
        return SubagentManager(
            llm=context.llm,
            tools=context.engine.tools,
            bus=self.bus,
            settings=context.subagent_settings,
            hooks=context.hooks,
            profiles=context.resource_loader.get_subagent_profiles(),
        )

    def _configure_context(self, context: RuntimeContext) -> None:
        """Attach runtime-owned services to the active engine/tool context."""
        context.engine.tool_context = ToolContext(
            llm=context.llm,
            engine=context.engine,
            agent=context.agent,
            session_manager=context.session_manager,
            resource_loader=context.resource_loader,
            extension_runtime=context.extension_runtime,
            hooks=context.hooks,
            subagent_manager=self.subagent_manager,
            bus=self.bus,
            cron=context.cron,
            mcp_manager=context.mcp_manager,
            memory_manager=context.memory_manager,
            process_manager=context.process_manager,
            settings_manager=context.settings_manager,
            auth_channel_manager=context.auth_channel_manager,
            acp_auth_manager=context.acp_auth_manager,
            acp_session_manager=context.acp_session_manager,
        )

    # -------------------------------------------------------------------------
    # Factory
    # -------------------------------------------------------------------------

    @classmethod
    async def create(
        cls,
        config: RuntimeConfig,
    ) -> Runtime:
        context = await RuntimeContext.create(config)
        runtime = cls(context=context, config=config)
        await runtime._emit_session_start('startup')
        return runtime

    # -------------------------------------------------------------------------
    # Public properties
    # -------------------------------------------------------------------------

    @property
    def current_session(self) -> Agent | None:
        return self._context.agent

    @property
    def session_manager(self):
        return self._context.session_manager

    @property
    def settings_manager(self):
        return self._context.settings_manager

    @property
    def auth_channel_manager(self):
        return self._context.auth_channel_manager

    @property
    def unified_session_enabled(self) -> bool:
        settings = self._context.settings_manager
        if settings is not None and settings.settings.unified_session is not None:
            return settings.settings.unified_session
        return True

    # -------------------------------------------------------------------------
    # Core input entry point
    # -------------------------------------------------------------------------

    async def user_input(self, text: str, options: PromptOptions | None = None) -> None:
        """
        Route user input. Slash commands go to CommandRegistry;
        everything else is forwarded to the active Agent.
        """
        parsed = parse_command(text)
        if parsed is not None:
            await self.commands.dispatch(parsed)
        else:
            await self.invoke(text, options)

    async def invoke(self, user_input: str, options: PromptOptions | None = None) -> None:
        """Forward a plain prompt to the current session."""
        if self._context.agent is None:
            raise RuntimeError("No active session available.")
        await self._context.agent.invoke(user_input, options)

    # -------------------------------------------------------------------------
    # Session lifecycle
    # -------------------------------------------------------------------------

    async def reload(self) -> None:
        """
        Reload all resources (tools, skills, commands, extensions, hooks)
        without touching the active session. The conversation history is
        preserved; only the runtime environment is refreshed.
        """
        resource_loader = self._context.resource_loader
        await resource_loader.reload()

        # Re-register file-based hooks (clear old registrations first).
        hooks = self._context.hooks
        hooks.clear()
        for event_type, handler in resource_loader.get_hooks():
            hooks.register(event_type, handler)

        # Rebuild the ExtensionRuntime with newly discovered extensions.
        from program.extension.runtime import ExtensionRuntime
        load_result = resource_loader.get_extensions()
        new_ext = ExtensionRuntime(load_result, self._context.agent, hooks=hooks)
        self._context.extension_runtime = new_ext
        if self._context.agent is not None:
            self._context.agent._extensions = new_ext

        # Swap the engine's tool list to pick up added/removed builtin tools.
        engine = self._context.engine
        new_tools = resource_loader.get_tools() + self._config.tools
        engine.tools = new_tools
        engine._tools = {t.name: t for t in new_tools}

        # Rebuild the command registry with the reloaded commands.
        self.commands = CommandRegistry(
            runtime=self,
            discovered=resource_loader.get_commands(),
        )
        self.commands.register_from_extensions(new_ext.get_commands())

        # Refresh named subagent profiles.
        self.subagent_manager.update_profiles(resource_loader.get_subagent_profiles())
        self._configure_context(self._context)

    async def new_session(self) -> None:
        """Shut down the current session and start a fresh one."""
        await self._emit_session_shutdown('new')
        self._config = self._config.model_copy(update={'session_file': None})
        self._context = await RuntimeContext.create(self._config)
        self.commands = CommandRegistry(
            runtime=self,
            discovered=self._context.resource_loader.get_commands(),
        )
        self.commands.register_from_extensions(
            self._context.extension_runtime.get_commands()
        )
        if self._context.agent is not None:
            self._context.agent._runtime = self
        self.mcp_manager = self._context.mcp_manager
        self.subagent_manager = self._create_subagent_manager(self._context)
        self._configure_context(self._context)
        await self._emit_session_start('new')

    async def resume_session(self, session_file: Path) -> None:
        """Shut down the current session and resume an existing one from a file."""
        before_results = await self._context.extension_runtime.emit(
            'session_before_switch',
            SessionBeforeSwitchEvent(reason='resume', target_session_file=str(session_file)),
        )
        for r in before_results:
            if isinstance(r, SessionBeforeSwitchResult) and r.cancel:
                return

        await self._emit_session_shutdown('resume')
        self._config = self._config.model_copy(update={'session_file': session_file})
        self._context = await RuntimeContext.create(self._config)
        self.commands = CommandRegistry(
            runtime=self,
            discovered=self._context.resource_loader.get_commands(),
        )
        self.commands.register_from_extensions(
            self._context.extension_runtime.get_commands()
        )
        if self._context.agent is not None:
            self._context.agent._runtime = self
        self.mcp_manager = self._context.mcp_manager
        self.subagent_manager = self._create_subagent_manager(self._context)
        self._configure_context(self._context)
        await self._emit_session_start('resume')

    async def fork_session(self, from_entry_id: str) -> None:
        """Branch the session tree at the given entry and start a new leaf."""
        sm = self._context.session_manager
        if from_entry_id not in sm.by_id:
            raise KeyError(f"Entry '{from_entry_id}' not found in session.")

        before_results = await self._context.extension_runtime.emit(
            'session_before_fork',
            SessionBeforeForkEvent(entry_id=from_entry_id),
        )
        for r in before_results:
            if isinstance(r, SessionBeforeForkResult) and r.cancel:
                return

        sm.branch(from_entry_id)
        await self._emit_session_start('fork')

    def create_session_agent(self) -> Agent:
        """
        Create an isolated agent for an ACP session.

        Shares LLM, tools, and resources with the main runtime context but
        isolates conversation state (session manager, hooks, extension runtime).
        Always uses an in-memory (non-persisted) session.
        """
        from program.hooks.service import Hooks
        from program.engine.service import Engine
        from program.engine.types import Options
        from program.session.manager import SessionManager
        from program.extension.runtime import ExtensionRuntime
        from program.runtime.types import _DeferredExtensionRuntime

        hooks = Hooks()

        engine = Engine(
            llm=self._context.llm,
            tools=list(self._context.engine.tools),
            options=Options(),
            hooks=hooks,
        )

        session_manager = SessionManager(
            cwd=self._context.session_manager.cwd,
            persist=False,
        )

        load_result = self._context.resource_loader.get_extensions()
        deferred = _DeferredExtensionRuntime(load_result)

        # Add a per-session MCP tool so each gateway session can independently
        # connect/disconnect servers without affecting other sessions.
        if self.mcp_manager is not None:
            from program.builtins.tools.mcp import MCPTool
            engine.add_tool(MCPTool(
                manager=self.mcp_manager,
                engine=engine,
                agent_id=str(id(engine)),
            ))

        # Add ACP agent tool if ACP services are available.
        from program.builtins.tools.acp_agent import ACPAgentTool
        if self._context.acp_session_manager is not None and self._context.acp_auth_manager is not None:
            engine.add_tool(ACPAgentTool(
                registry=self._context.settings_manager.get_acp_agents() if self._context.settings_manager else [],
                session_manager=self._context.acp_session_manager,
                auth_manager=self._context.acp_auth_manager,
                bus=self.bus,
                agent=None,
                settings_manager=self._context.settings_manager,
            ))

        agent = Agent(
            engine=engine,
            session_manager=session_manager,
            resource_loader=self._context.resource_loader,
            extension_runtime=deferred,  # type: ignore[arg-type]
            compaction=self._context.compaction,
            config=self._context.agent._config,
        )

        real_ext = ExtensionRuntime(load_result, agent, hooks=hooks)
        agent._extensions = real_ext
        agent._runtime = self
        engine.tool_context = ToolContext(
            llm=self._context.llm,
            engine=engine,
            agent=agent,
            session_manager=session_manager,
            resource_loader=self._context.resource_loader,
            extension_runtime=real_ext,
            hooks=hooks,
            subagent_manager=self.subagent_manager,
            bus=self.bus,
            cron=self._context.cron,
            mcp_manager=self.mcp_manager,
            memory_manager=self._context.memory_manager,
            process_manager=self._context.process_manager,
            settings_manager=self._context.settings_manager,
            auth_channel_manager=self._context.auth_channel_manager,
            acp_auth_manager=self._context.acp_auth_manager,
            acp_session_manager=self._context.acp_session_manager,
        )

        return agent

    def shutdown(self) -> None:
        """Stop runtime-owned background services. Call when exiting the REPL."""
        if self._context.cron is not None:
            self._context.cron.stop()
        if self.mcp_manager is not None:
            import asyncio
            asyncio.get_event_loop().create_task(self.mcp_manager.disconnect_all())

    async def ashutdown(self) -> None:
        """Await full teardown of runtime-owned services."""
        if self._context.cron is not None:
            self._context.cron.stop()
        if self._context.process_manager is not None:
            await self._context.process_manager.close()
        if self.mcp_manager is not None:
            try:
                await self.mcp_manager.disconnect_all()
            except Exception:
                pass
        if self._context.memory_manager is not None:
            await self._context.memory_manager.shutdown()

    # -------------------------------------------------------------------------
    # Cron
    # -------------------------------------------------------------------------

    async def _handle_cron_job(self, job: CronJob) -> None:
        """Inject a cron job's message into the agent and route the response."""
        import logging
        logger = logging.getLogger(__name__)
        logger.info('Cron job invoking agent | id=%s name=%s', job.id, job.name)

        channel_id = job.payload.channel_id
        chat_id = job.payload.chat_id

        from program.bus.types import IncomingMessage, OutgoingMessage, TextPart
        target_channel = channel_id or 'stdio'
        target_chat_id = chat_id or 'cli'

        if job.payload.deliver:
            await self.bus.publish_outgoing(OutgoingMessage(
                channel=target_channel,
                chat_id=target_chat_id,
                parts=[TextPart(content=job.payload.message)],
            ))
        else:
            await self.bus.publish_incoming(IncomingMessage(
                channel=target_channel,
                chat_id=target_chat_id,
                parts=[TextPart(content=job.payload.message)],
                metadata={'source': 'cron', 'job_id': job.id},
            ))

    # -------------------------------------------------------------------------
    # Extension event helpers
    # -------------------------------------------------------------------------

    async def _emit_session_start(self, reason: str) -> None:
        await self._context.extension_runtime.emit(
            'session_start',
            SessionStartEvent(reason=reason),  # type: ignore[arg-type]
        )
        self._maybe_run_curator()

    def _maybe_run_curator(self) -> None:
        try:
            from program.skill.curator import maybe_run_curator
            ctx = self._context
            sm = ctx.settings_manager
            curator_cfg = sm.settings.curator if sm else None
            if curator_cfg is not None and not curator_cfg.enabled:
                return
            tools_by_name = {t.name: t for t in ctx.resource_loader.get_tools()}
            skill_manage = tools_by_name.get('skill_manage')
            if skill_manage is None:
                return
            maybe_run_curator(
                llm=ctx.llm,
                skill_manage_tool=skill_manage,
                skill_view_tool=tools_by_name.get('skill_view'),
                interval_hours=curator_cfg.interval_hours if curator_cfg else 168,
                paused=curator_cfg.paused if curator_cfg else False,
                stale_after_days=curator_cfg.stale_after_days if curator_cfg else 30,
                archive_after_days=curator_cfg.archive_after_days if curator_cfg else 90,
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).debug('curator check failed: %s', exc)

    async def _emit_session_shutdown(self, reason: str) -> None:
        await self._context.extension_runtime.emit(
            'session_shutdown',
            SessionShutdownEvent(reason=reason),  # type: ignore[arg-type]
        )
        if self._context.memory_manager:
            from program.session.types import MessageEntry
            entries = self._context.session_manager.get_branch()
            messages_raw = [
                e.model_dump()
                for e in entries
                if isinstance(e, MessageEntry)
            ]
            await self._context.memory_manager.on_session_end(messages_raw)

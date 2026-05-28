from __future__ import annotations

from pathlib import Path

from operator_use.runtime.types import RuntimeConfig, RuntimeContext
from operator_use.agent.service import Agent
from operator_use.agent.types import PromptOptions
from operator_use.bus.service import Bus
from operator_use.cron.types import CronJob
from operator_use.commands.registry import CommandRegistry
from operator_use.subagent.manager import SubagentManager
from operator_use.workflow.manager import WorkflowManager
from operator_use.commands.types import parse_command
from operator_use.extension.types import (
    SessionStartEvent, SessionShutdownEvent, SessionBeforeSwitchEvent,
    SessionBeforeSwitchResult, SessionBeforeForkEvent, SessionBeforeForkResult,
)
from operator_use.tool.types import ToolContext
from operator_use.team.manager import TeamManager
from operator_use.peer.manager import PeerSessionManager
from operator_use.settings.paths import get_config_dir
from operator_use.agent.profile import AgentProfile


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
        self.workflow_manager = self._create_workflow_manager(context)
        _profile = context.resource_loader._active_profile
        _teams_dir = _profile.teams_dir if _profile else None
        self.team_manager = TeamManager(_teams_dir)
        # Expose MCPManager for use in create_session_agent() (gateway + ACP) and shutdown.
        self.mcp_manager = context.mcp_manager
        self._agent_profiles: dict[str, AgentProfile] = {
            p.name: p for p in context.resource_loader.get_agent_profiles()
        }
        # Registry of built peer agents — populated lazily or by GatewayManager.
        self._peer_agents: dict[str, Agent] = {}
        self._configure_context(context)

    def _create_workflow_manager(self, context: RuntimeContext) -> WorkflowManager:
        profile = context.resource_loader._active_profile
        return WorkflowManager(
            llm=context.llm,
            tools=context.engine.tools,
            bus=self.bus,
            workflows_dir=profile.workflows_dir if profile else None,
        )

    def _create_subagent_manager(self, context: RuntimeContext) -> SubagentManager:
        return SubagentManager(
            llm=context.llm,
            tools=context.engine.tools,
            bus=self.bus,
            settings=context.subagent_settings,
            hooks=context.hooks,
            profiles=context.resource_loader.get_subagent_profiles(),
        )

    def _create_desktop_instance(self, context: RuntimeContext):
        """Instantiate a Desktop from settings, or None if computer-use is disabled."""
        sm = context.settings_manager
        if sm is None:
            return None
        s = sm.settings
        # Legacy flat flag still respected; new settings object takes precedence.
        cu = s.computer_use
        if cu is None:
            from operator_use.settings.types import ComputerUseSettings
            cu = ComputerUseSettings()
        if not cu.enabled:
            return None
        import operator_use.computer as computer_module
        return computer_module.Desktop(
            use_screenshot=cu.use_screenshot,
            use_accessibility=cu.use_accessibility,
            use_annotation=cu.use_annotation,
        )

    def _create_browser_instance(self, context: RuntimeContext):
        """Instantiate a Browser from settings, or None if browser-use is disabled."""
        sm = context.settings_manager
        if sm is None:
            return None
        s = sm.settings
        bu = s.browser_use
        if bu is None:
            from operator_use.settings.types import BrowserUseSettings
            bu = BrowserUseSettings()
        if not bu.enabled:
            return None
        from operator_use.browser.client.service import Browser
        from operator_use.browser.client.config import BrowserConfig
        return Browser(
            config=BrowserConfig(
                use_screenshot=bu.use_screenshot,
                use_accessibility=bu.use_accessibility,
                headless=bu.headless,
                browser=bu.browser,
                cdp_port=bu.cdp_port,
                attach_to_existing=bu.attach_to_existing,
            ),
        )

    def _configure_context(self, context: RuntimeContext) -> None:
        """Attach runtime-owned services to the active engine/tool context, then drop tools whose backing service is absent."""
        tool_ctx = ToolContext(
            llm=context.llm,
            engine=context.engine,
            agent=context.agent,
            session_manager=context.session_manager,
            resource_loader=context.resource_loader,
            extension_runtime=context.extension_runtime,
            hooks=context.hooks,
            subagent_manager=self.subagent_manager,
            workflow_manager=self.workflow_manager,
            bus=self.bus,
            cron=context.cron,
            mcp_manager=context.mcp_manager,
            memory_manager=context.memory_manager,
            desktop=self._create_desktop_instance(context),
            browser=self._create_browser_instance(context),
            process_manager=context.process_manager,
            settings_manager=context.settings_manager,
            auth_channel_manager=context.auth_channel_manager,
            acp_auth_manager=context.acp_auth_manager,
            acp_session_manager=context.acp_session_manager,
            peer_session_manager=context.peer_session_manager,
            peer_agents=self._peer_agents,
            team_manager=self.team_manager,
        )
        context.engine.tool_context = tool_ctx
        for tool in list(context.engine.state.tools):
            if not tool.is_available(tool_ctx):
                context.engine.remove_tool(tool.name)

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

    def get_session_dir(self, agent: Agent | None = None) -> Path:
        """Return the sessions directory for the given agent (or current session).

        Profile agents have their own isolated sessions_dir under
        ~/.operator/profiles/<name>/sessions/.
        """
        target = agent or self._context.agent
        if target is not None:
            active = target.get_active_profile()
            if active is not None:
                return active.sessions_dir
            sm = target._session_manager
            if sm is not None and sm.session_dir is not None:
                return sm.session_dir
        sm = self._context.session_manager
        if sm is not None and sm.session_dir is not None:
            return sm.session_dir
        raise RuntimeError('No active profile — cannot determine session directory')

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
        from operator_use.extension.runtime import ExtensionRuntime
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
        # Refresh workflow tool list so new tools are visible to agent() calls.
        self.workflow_manager.update_tools(new_tools)
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
        self.workflow_manager = self._create_workflow_manager(self._context)
        self._configure_context(self._context)
        await self._emit_session_start('new')

    async def resume_session(self, session_file: Path) -> None:
        """Shut down the current session and resume an existing one from a file.

        If the active agent belongs to a profile, the session file must live
        inside that profile's sessions_dir. Cross-profile access is silently
        rejected to prevent one profile from reading another's history.
        """
        session_file = Path(session_file).resolve()
        permitted_dir = self.get_session_dir()
        try:
            session_file.relative_to(permitted_dir)
        except ValueError:
            import logging
            logging.getLogger(__name__).warning(
                'resume_session: rejected — %s is outside permitted dir %s',
                session_file, permitted_dir,
            )
            return

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
        self.workflow_manager = self._create_workflow_manager(self._context)
        self._configure_context(self._context)
        await self._restore_agent_profile_from_session(self._context)
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
        Create an isolated agent for a new session (gateway channel or ACP).

        Shares LLM, tools, and resources with the main runtime context but
        isolates conversation state (session manager, hooks, extension runtime).
        Always uses an in-memory (non-persisted) session.
        """
        from operator_use.hooks.service import Hooks
        from operator_use.engine.service import Engine
        from operator_use.engine.types import Options
        from operator_use.session.manager import SessionManager
        from operator_use.extension.runtime import ExtensionRuntime
        from operator_use.runtime.types import _DeferredExtensionRuntime

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
            from operator_use.builtins.tools.mcp import MCPTool
            engine.add_tool(MCPTool(
                manager=self.mcp_manager,
                engine=engine,
                agent_id=str(id(engine)),
            ))

        # Add ACP agent tool if ACP services are available.
        from operator_use.builtins.tools.acp_agent import ACPAgentTool
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
            workflow_manager=self.workflow_manager,
            bus=self.bus,
            cron=self._context.cron,
            mcp_manager=self.mcp_manager,
            memory_manager=self._context.memory_manager,
            desktop=self._create_desktop_instance(self._context),
            browser=self._create_browser_instance(self._context),
            process_manager=self._context.process_manager,
            settings_manager=self._context.settings_manager,
            auth_channel_manager=self._context.auth_channel_manager,
            acp_auth_manager=self._context.acp_auth_manager,
            acp_session_manager=self._context.acp_session_manager,
            team_manager=self.team_manager,
        )

        return agent

    def create_acp_session_agent(self, sessions_dir: Path, session_id: str) -> Agent:
        """
        Create a persistent agent for a server-side ACP session.

        Unlike create_session_agent() which is always in-memory, this agent writes
        conversation history to *sessions_dir*/<session_id>.jsonl so sessions survive
        restarts.  If the file already exists the conversation is resumed from it.

        The session_id is the ACP-level UUID assigned by new_session() — it becomes
        the filename, giving a deterministic sessions_dir/<session_id>.jsonl mapping.
        """
        from operator_use.hooks.service import Hooks
        from operator_use.engine.service import Engine
        from operator_use.engine.types import Options
        from operator_use.session.manager import SessionManager
        from operator_use.extension.runtime import ExtensionRuntime
        from operator_use.runtime.types import _DeferredExtensionRuntime

        hooks = Hooks()

        engine = Engine(
            llm=self._context.llm,
            tools=list(self._context.engine.tools),
            options=Options(),
            hooks=hooks,
        )

        sessions_dir.mkdir(parents=True, exist_ok=True)
        session_file = sessions_dir / f'{session_id}.jsonl'
        session_manager = SessionManager(
            cwd=self._context.session_manager.cwd,
            session_dir=sessions_dir,
            session_file=session_file,
            persist=True,
        )

        load_result = self._context.resource_loader.get_extensions()
        deferred = _DeferredExtensionRuntime(load_result)

        if self.mcp_manager is not None:
            from operator_use.builtins.tools.mcp import MCPTool
            engine.add_tool(MCPTool(
                manager=self.mcp_manager,
                engine=engine,
                agent_id=str(id(engine)),
            ))

        from operator_use.builtins.tools.acp_agent import ACPAgentTool
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
            workflow_manager=self.workflow_manager,
            bus=self.bus,
            cron=self._context.cron,
            mcp_manager=self.mcp_manager,
            memory_manager=self._context.memory_manager,
            desktop=self._create_desktop_instance(self._context),
            browser=self._create_browser_instance(self._context),
            process_manager=self._context.process_manager,
            settings_manager=self._context.settings_manager,
            auth_channel_manager=self._context.auth_channel_manager,
            acp_auth_manager=self._context.acp_auth_manager,
            acp_session_manager=self._context.acp_session_manager,
            team_manager=self.team_manager,
        )

        return agent

    async def create_profile_agent(self, profile: AgentProfile) -> Agent:
        """
        Create a persistent agent for a named profile.

        Unlike create_session_agent() (which is always in-memory), this agent:
        - Uses a persisted SessionManager rooted at profile.sessions_dir
        - Continues from the most recent session if one exists
        - Loads resources exclusively from the profile directory (after reload())
        - Applies the profile's tool allowlist if specified
        """
        from operator_use.hooks.service import Hooks
        from operator_use.engine.service import Engine
        from operator_use.engine.types import Options
        from operator_use.session.manager import SessionManager
        from operator_use.extension.runtime import ExtensionRuntime
        from operator_use.resource.loader import ResourceLoader
        from operator_use.resource.types import ResourceLoaderOptions
        from operator_use.compaction.strategy.summarization.service import SummarizationCompaction
        from operator_use.compaction.strategy.types import CompactionSettings as CmpSettings
        from operator_use.runtime.types import _DeferredExtensionRuntime
        from operator_use.session.utils import find_most_recent_session
        from operator_use.agent.types import AgentConfig
        from operator_use.settings.paths import get_config_dir

        hooks = Hooks()

        # Resolve profile LLM (use profile model override if specified)
        llm = self._context.llm
        if profile.model_id:
            try:
                from operator_use.inference.api.text.service import LLM
                llm = LLM(
                    model_id=profile.model_id,
                    provider=profile.provider,
                    auth_store=llm._auth_store,
                )
            except Exception:
                pass  # fall back to default LLM

        # ResourceLoader scoped to the profile directory — must reload() before
        # any tool/skill/extension discovery so profile-local resources are found.
        resource_loader = ResourceLoader(
            ResourceLoaderOptions(
                cwd=profile.sessions_dir,
                config_dir=get_config_dir(),
            )
        )
        resource_loader.set_active_profile(profile)
        await resource_loader.reload()

        # Build the engine's tool list: start with builtins from the reloaded
        # resource loader (profile-scoped), then apply the profile's allowlist.
        all_tools = resource_loader.get_tools()
        if profile.tools:
            allowed = set(profile.tools)
            all_tools = [t for t in all_tools if t.name in allowed]

        engine = Engine(
            llm=llm,
            tools=all_tools,
            options=Options(),
            hooks=hooks,
        )

        # Persisted session in profile's sessions/ directory
        sessions_dir = profile.sessions_dir
        sessions_dir.mkdir(parents=True, exist_ok=True)
        most_recent = find_most_recent_session(sessions_dir)
        session_manager = SessionManager(
            cwd=sessions_dir,
            session_dir=sessions_dir,
            session_file=most_recent,
            persist=True,
        )

        load_result = resource_loader.get_extensions()
        deferred = _DeferredExtensionRuntime(load_result)

        compaction_settings = CmpSettings(enabled=False)
        if self._context.settings_manager:
            s = self._context.settings_manager.settings
            if s.compaction and s.compaction.enabled is not None:
                compaction_settings = CmpSettings(
                    enabled=bool(s.compaction.enabled),
                    strategy=s.compaction.strategy,
                )
        compaction = SummarizationCompaction(
            llm=llm,
            settings=compaction_settings,
        )

        config = self._context.agent._config if self._context.agent else AgentConfig(
            cwd=sessions_dir,
            retry_enabled=True,
        )

        agent = Agent(
            engine=engine,
            session_manager=session_manager,
            resource_loader=resource_loader,
            extension_runtime=deferred,  # type: ignore[arg-type]
            compaction=compaction,
            config=config,
        )

        real_ext = ExtensionRuntime(load_result, agent, hooks=hooks)
        agent._extensions = real_ext
        agent._runtime = self
        agent._active_profile = profile

        engine.tool_context = ToolContext(
            llm=llm,
            engine=engine,
            agent=agent,
            session_manager=session_manager,
            resource_loader=resource_loader,
            extension_runtime=real_ext,
            hooks=hooks,
            subagent_manager=self.subagent_manager,
            workflow_manager=self.workflow_manager,
            bus=self.bus,
            cron=self._context.cron,
            mcp_manager=self.mcp_manager,
            memory_manager=self._context.memory_manager,
            desktop=self._create_desktop_instance(self._context),
            browser=self._create_browser_instance(self._context),
            process_manager=self._context.process_manager,
            settings_manager=self._context.settings_manager,
            auth_channel_manager=self._context.auth_channel_manager,
            acp_auth_manager=self._context.acp_auth_manager,
            acp_session_manager=self._context.acp_session_manager,
            peer_session_manager=PeerSessionManager(profile.peer_dir),
            peer_agents=self._peer_agents,
            team_manager=self.team_manager,
        )

        # Drop any tools whose backing service is unavailable in this context
        for tool in list(engine.state.tools):
            if not tool.is_available(engine.tool_context):
                engine.remove_tool(tool.name)

        return agent

    # -------------------------------------------------------------------------
    # Agent profile helpers
    # -------------------------------------------------------------------------

    def get_agent_profiles(self) -> dict[str, AgentProfile]:
        return self._agent_profiles

    # ── Peer agent registry ───────────────────────────────────────────────────

    def register_peer_agent(self, name: str, agent: Agent) -> None:
        """Register a pre-built profile agent so peers can call it directly."""
        self._peer_agents[name] = agent

    async def get_or_build_peer_agent(self, name: str) -> Agent:
        """Return the live peer agent for *name*, building it lazily if needed.

        Called by the peer_agent tool.  GatewayManager pre-populates the
        registry at startup; in REPL mode agents are built on first access.
        """
        if name in self._peer_agents:
            return self._peer_agents[name]
        profile = self._agent_profiles.get(name)
        if profile is None:
            raise ValueError(f"No profile named '{name}'. Known profiles: {list(self._agent_profiles)}")
        agent = await self.create_profile_agent(profile)
        self._peer_agents[name] = agent
        return agent

    def get_active_agent_profile(self) -> AgentProfile | None:
        agent = self._context.agent
        return agent.get_active_profile() if agent else None

    async def _restore_agent_profile_from_session(self, context: RuntimeContext) -> None:
        """Re-apply the last agent profile recorded in the session entries."""
        if context.agent is None or context.session_manager is None:
            return
        from operator_use.session.types import CustomInfoEntry
        for entry in reversed(context.session_manager.entries):
            if isinstance(entry, CustomInfoEntry) and entry.custom_type == 'agent_profile':
                name = (entry.data or {}).get('name')
                profile = self._agent_profiles.get(name) if name else None
                if profile:
                    await context.agent.apply_profile(profile)
                return

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

        from operator_use.bus.types import IncomingMessage, OutgoingMessage, TextPart
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
            from operator_use.skill.curator import maybe_run_curator
            ctx = self._context
            sm = ctx.settings_manager
            curator_cfg = sm.settings.curator if sm else None
            if curator_cfg is not None and not curator_cfg.enabled:
                return
            tools_by_name = {t.name: t for t in ctx.resource_loader.get_tools()}
            skill_tool = tools_by_name.get('skill')
            if skill_tool is None:
                return
            profile = ctx.resource_loader._active_profile
            if profile is None:
                return
            maybe_run_curator(
                skills_dir=profile.skills_dir,
                llm=ctx.llm,
                skill_manage_tool=skill_tool,
                skill_view_tool=skill_tool,
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
            from operator_use.session.types import MessageEntry
            entries = self._context.session_manager.get_branch()
            messages_raw = [
                e.model_dump()
                for e in entries
                if isinstance(e, MessageEntry)
            ]
            await self._context.memory_manager.on_session_end(messages_raw)

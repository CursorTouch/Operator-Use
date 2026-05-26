from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from program.tool.types import Tool
from program.agent.service import Agent
from program.agent.types import AgentConfig
from program.compaction.compact import Compaction
from program.compaction.types import CompactionSettings
from program.engine.service import Engine
from program.engine.types import Options
from program.extension.loader import discover_and_load_extensions
from program.extension.runtime import ExtensionRuntime
from program.extension.types import LoadExtensionsResult
from program.hooks.service import Hooks
from program.inference.api.text.service import LLM
from program.resource.loader import ResourceLoader
from program.resource.types import ResourceLoaderOptions
from program.session.manager import SessionManager
from program.settings.manager import SettingsManager
from program.cron.scheduler import CronScheduler as Cron
from program.auth.channels import ChannelAuthManager
from program.auth.acp import ACPAuthManager
from program.subagent.manager import SubagentManager
from program.subagent.types import SubagentSettings
from program.mcp.manager import MCPManager
from program.memory.manager import MemoryManager
from program.memory.provider.registry import MemoryProviderRegistry
from program.memory.api.registry import MemoryAPIRegistry
from program.memory.types import MemoryOptions, MemoryRuntimeContext
from program.acp.manager import ACPSessionManager
from program.process.manager import ProcessManager
from program.settings.paths import (
    get_config_dir, get_crons_path, get_acp_sessions_dir,
    get_channels_auth_path, get_acp_auth_path, get_packages_dir,
)


class RuntimeConfig(BaseModel):
    """Public config passed to RuntimeContext.create()."""
    model_config = {'arbitrary_types_allowed': True}

    cwd: Path
    config_dir: Path | None = None

    # LLM
    model_id: str = 'claude-sonnet-4-6'
    provider: str | None = None

    # Session
    session_file: Path | None = None
    persist_session: bool = True
    resume: bool = False  # resume the most recent session instead of starting fresh

    # Tools & prompt
    tools: list[Tool] = Field(default_factory=list)
    prompt_guidelines: list[str] = Field(default_factory=list)

    # Resource loader
    no_extensions: bool = False
    no_skills: bool = False
    no_context_files: bool = False
    system_prompt: str | None = None
    append_system_prompt: list[str] = Field(default_factory=list)

    # Sandbox
    sandbox: str | None = None  # 'strict' | 'enforce' | 'warn' | None (off)

    # Compaction
    compaction_enabled: bool = True
    compaction_reserve_tokens: int = 16384
    compaction_keep_recent_tokens: int = 20000


class RuntimeContext:
    """
    Constructs and owns all dependencies for one Agent.

    Usage:
        ctx = await RuntimeContext.create(config)
        agent = ctx.agent
        await agent.prompt("hello")
    """

    def __init__(
        self,
        agent: Agent,
        llm: LLM,
        engine: Engine,
        session_manager: SessionManager,
        resource_loader: ResourceLoader,
        extension_runtime: ExtensionRuntime,
        compaction: Compaction,
        settings_manager: SettingsManager | None,
        cron: Cron | None = None,
        hooks: Hooks | None = None,
        auth_channel_manager: ChannelAuthManager | None = None,
        subagent_settings: SubagentSettings | None = None,
        mcp_manager: MCPManager | None = None,
        memory_manager: MemoryManager | None = None,
        acp_auth_manager: ACPAuthManager | None = None,
        acp_session_manager: ACPSessionManager | None = None,
        process_manager: ProcessManager | None = None,
    ) -> None:
        self.agent = agent
        self.llm = llm
        self.engine = engine
        self.session_manager = session_manager
        self.resource_loader = resource_loader
        self.extension_runtime = extension_runtime
        self.compaction = compaction
        self.settings_manager = settings_manager
        self.cron = cron
        self.hooks: Hooks = hooks or extension_runtime._hooks
        self.auth_channel_manager = auth_channel_manager
        self.subagent_settings = subagent_settings or SubagentSettings()
        self.mcp_manager: MCPManager | None = mcp_manager
        self.memory_manager: MemoryManager | None = memory_manager
        self.acp_auth_manager: ACPAuthManager | None = acp_auth_manager
        self.acp_session_manager: ACPSessionManager | None = acp_session_manager
        self.process_manager: ProcessManager | None = process_manager

    @classmethod
    async def create(
        cls,
        config: RuntimeConfig,
        settings_manager: SettingsManager | None = None,
    ) -> RuntimeContext:
        cwd = config.cwd.resolve()
        config_dir = (config.config_dir or get_config_dir()).resolve()

        # ── Settings ──────────────────────────────────────────────────────────
        if settings_manager is None:
            settings_manager = SettingsManager.create(cwd, config_dir)

        # ── Resource loader ───────────────────────────────────────────────────
        # Extensions must load before LLM so they can register custom providers.
        ext_entries = settings_manager.get_extension_list()
        disabled_stems = {(e.name or Path(e.path).stem) for e in ext_entries if not e.enabled}
        entry_configs = {(e.name or Path(e.path).stem): (e.settings or {}) for e in ext_entries}
        loader_opts = ResourceLoaderOptions(
            cwd=cwd,
            config_dir=config_dir,
            no_extensions=config.no_extensions or not settings_manager.is_extensions_enabled(),
            no_skills=config.no_skills,
            no_context_files=config.no_context_files,
            system_prompt=config.system_prompt,
            append_system_prompt=config.append_system_prompt,
            disabled_extension_stems=disabled_stems,
            extension_configs=entry_configs,
            package_sources=settings_manager.get_packages(),
            packages_dir=get_packages_dir(),
        )
        resource_loader = ResourceLoader(loader_opts)
        await resource_loader.reload()

        # ── Hooks ─────────────────────────────────────────────────────────────
        hooks = Hooks()
        for event_type, handler in resource_loader.get_hooks():
            hooks.register(event_type, handler)

        # ── Sandbox ───────────────────────────────────────────────────────────
        if config.sandbox and config.sandbox != 'off':
            from program.sandbox import Sandbox, SandboxPolicy
            policy = (
                SandboxPolicy.strict(cwd) if config.sandbox == 'strict'
                else SandboxPolicy(mode=config.sandbox)  # type: ignore[arg-type]
            )
            Sandbox(policy, cwd).register(hooks)

        # ── Extension runtime ─────────────────────────────────────────────────
        load_result = resource_loader.get_extensions()
        extension_runtime = _DeferredExtensionRuntime(load_result)

        # ── Apply extension provider registrations ────────────────────────────
        # Extensions (including those from packages) may register custom inference
        # and memory providers via api.register_provider() / api.register_memory_provider().
        # Apply these to the class-level LLM registries before constructing LLM so
        # that a user's configured provider is resolvable at LLM() construction time.
        for _provider in extension_runtime.get_providers():
            LLM._providers.register(_provider)
        for _api_name, _api_cls in extension_runtime.get_text_apis().items():
            LLM._apis.register(_api_name, _api_cls)

        # ── LLM ───────────────────────────────────────────────────────────────
        model_id = config.model_id or settings_manager.get_default_model() or RuntimeConfig.model_fields["model_id"].default
        provider = config.provider or settings_manager.get_default_provider()
        llm = LLM(model_id=model_id, provider=provider)

        # ── Compaction ────────────────────────────────────────────────────────
        # Resolve compaction settings live from the SettingsManager so that
        # changes to settings.json (e.g. {"compaction": {"enabled": false}})
        # take effect on the next check instead of being frozen at startup.
        # The RuntimeConfig values act as overrides: an explicit False/non-default
        # there still wins over the persisted setting.
        def _resolve_compaction_settings() -> CompactionSettings:
            persisted = settings_manager.get_compaction_settings()
            return CompactionSettings(
                enabled=config.compaction_enabled and persisted["enabled"],
                reserve_tokens=persisted["reserve_tokens"],
                keep_recent_tokens=persisted["keep_recent_tokens"],
            )

        compaction = Compaction(
            llm=llm,
            settings=_resolve_compaction_settings(),
            settings_provider=_resolve_compaction_settings,
        )

        # ── Session manager ───────────────────────────────────────────────────
        session_dir = settings_manager.get_session_dir()
        if config.resume and not config.session_file and config.persist_session:
            session_manager = SessionManager.continue_recent(cwd, session_dir)
        else:
            session_manager = SessionManager(
                cwd=cwd,
                session_dir=session_dir,
                session_file=config.session_file,
                persist=config.persist_session,
            )

        # ── Auth ─────────────────────────────────────────────────────────────
        auth_channel_manager = ChannelAuthManager(get_channels_auth_path())
        acp_auth_manager = ACPAuthManager(get_acp_auth_path())
        acp_session_manager = ACPSessionManager(get_acp_sessions_dir())

        # ── Cron ─────────────────────────────────────────────────────────────
        cron: Cron | None = None
        all_tools = resource_loader.get_tools() + config.tools
        if settings_manager.get_cron_enabled():
            cron = Cron(store_path=get_crons_path(config_dir))
            # Wire cron into the tool instance from the resource loader — the loader
            # registers the module under a different name than the package import, so
            # importing via 'from program.builtins.tools.cron import tool' would give
            # a distinct object that the engine never sees.
            _cron_tool = next((t for t in all_tools if t.name == 'cron'), None)
            if _cron_tool is not None:
                _cron_tool._cron = cron  # type: ignore[attr-defined]
        else:
            all_tools = [t for t in all_tools if t.name != 'cron']

        # ── Process manager ───────────────────────────────────────────────────
        process_manager = ProcessManager(default_cwd=str(cwd))
        _process_tool = next((t for t in all_tools if t.name == 'process'), None)
        if _process_tool is not None:
            _process_tool._manager = process_manager  # type: ignore[attr-defined]
        _terminal_tool = next((t for t in all_tools if t.name == 'terminal'), None)
        if _terminal_tool is not None:
            _terminal_tool._manager = process_manager  # type: ignore[attr-defined]
            _terminal_tool._execute_path = settings_manager.get_execute_path()  # type: ignore[attr-defined]
            _terminal_tool._execute_command_prefix = settings_manager.get_execute_command_prefix()  # type: ignore[attr-defined]

        # ── MCP ───────────────────────────────────────────────────────────────
        mcp_configs = settings_manager.get_mcp_servers()
        mcp_manager: MCPManager | None = MCPManager(mcp_configs) if mcp_configs else None

        # ── Memory ────────────────────────────────────────────────────────────
        memory_settings = settings_manager.get_memory_settings()
        memory_enabled = True if memory_settings.enabled is None else memory_settings.enabled
        memory_manager: MemoryManager | None = None
        if memory_enabled:
            provider_id = memory_settings.provider
            # Build registries seeded from builtins, then add any providers/APIs
            # registered by extensions (including those bundled in packages).
            _mem_providers = MemoryProviderRegistry.from_builtins()
            _mem_apis = MemoryAPIRegistry.from_builtins()
            for _mp in extension_runtime.get_memory_providers():
                _mem_providers.register(_mp)
            for _ma_name, _ma_cls in extension_runtime.get_memory_apis().items():
                _mem_apis.register(_ma_name, _ma_cls)
            memory_manager = MemoryManager(provider_id=provider_id, providers=_mem_providers, apis=_mem_apis)
            memory_provider = memory_manager.providers.get(provider_id) if provider_id else None
            memory_options = memory_provider.options if memory_provider is not None else None
            if memory_options is not None:
                if memory_settings.max_prompt_chars is not None:
                    memory_options.max_prompt_chars = memory_settings.max_prompt_chars
                if memory_settings.sync_turns is not None:
                    memory_options.sync_turns = memory_settings.sync_turns
                if memory_settings.prefetch is not None:
                    memory_options.prefetch = memory_settings.prefetch
            try:
                memory_manager.initialize(MemoryRuntimeContext(
                    cwd=cwd,
                    session_id=session_manager.session_id or "",
                    project_memory_dir=get_config_dir(cwd) / "memory",
                    global_memory_dir=get_config_dir() / "memory",
                ))
            except ImportError:
                memory_manager = None

        # ── Agent loop ────────────────────────────────────────────────────────
        engine = Engine(
            llm=llm,
            tools=all_tools,
            options=Options(),
            hooks=hooks,
        )

        # ── MCP builtin tool (per-engine, so the agent can manage connections) ─
        if mcp_manager is not None:
            from program.builtins.tools.mcp import MCPTool
            engine.add_tool(MCPTool(
                manager=mcp_manager,
                engine=engine,
                agent_id=str(id(engine)),
            ))

        # ── ACP agent tool ─────────────────────────────────────────────────────
        from program.builtins.tools.acp_agent import ACPAgentTool
        engine.add_tool(ACPAgentTool(
            registry=settings_manager.get_acp_agents(),
            session_manager=acp_session_manager,
            auth_manager=acp_auth_manager,
            bus=None,   # Runtime attaches the shared bus through ToolContext.
            agent=None,
            settings_manager=settings_manager,
        ))

        # ── Agent config ──────────────────────────────────────────────────────
        agent_config = AgentConfig(
            cwd=cwd,
            model=llm.model,
            context_window=llm.model.context_window or 200_000,
            prompt_guidelines=config.prompt_guidelines,
            retry_enabled=settings_manager.get_retry_enabled(),
            retry_max_retries=settings_manager.get_retry_max_retries(),
            retry_base_delay_ms=settings_manager.get_retry_base_delay_ms(),
        )

        # ── Wire everything together ──────────────────────────────────────────
        agent = Agent(
            engine=engine,
            session_manager=session_manager,
            resource_loader=resource_loader,
            extension_runtime=extension_runtime,  # type: ignore[arg-type]
            compaction=compaction,
            config=agent_config,
        )

        real_runtime = ExtensionRuntime(load_result, agent, hooks=hooks)
        agent._extensions = real_runtime

        return cls(
            agent=agent,
            llm=llm,
            engine=engine,
            session_manager=session_manager,
            resource_loader=resource_loader,
            extension_runtime=real_runtime,
            compaction=compaction,
            settings_manager=settings_manager,
            cron=cron,
            hooks=hooks,
            auth_channel_manager=auth_channel_manager,
            subagent_settings=SubagentSettings(),
            mcp_manager=mcp_manager,
            memory_manager=memory_manager,
            acp_auth_manager=acp_auth_manager,
            acp_session_manager=acp_session_manager,
            process_manager=process_manager,
        )


class _DeferredExtensionRuntime(ExtensionRuntime):
    """No-op runtime used before the real context (Agent) is available."""

    def __init__(self, load_result: LoadExtensionsResult) -> None:
        class _NullCtx:
            pass
        super().__init__(load_result, _NullCtx())  # type: ignore[arg-type]

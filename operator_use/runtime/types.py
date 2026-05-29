from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from operator_use.tool.types import Tool
from operator_use.agent.service import Agent
from operator_use.agent.types import AgentConfig
from operator_use.agent.profile import AgentProfile, create_ephemeral_profile
from operator_use.compaction.strategy.base import Compaction
from operator_use.compaction.strategy.summarization.service import SummarizationCompaction
from operator_use.compaction.strategy.types import CompactionSettings
from operator_use.engine.service import Engine
from operator_use.engine.types import Options
from operator_use.extension.loader import discover_and_load_extensions
from operator_use.extension.runtime import ExtensionRuntime
from operator_use.extension.types import LoadExtensionsResult
from operator_use.hooks.service import Hooks
from operator_use.auth.providers import ProviderAuthManager
from operator_use.inference.api.text.service import LLM
from operator_use.inference.api.text.registry import LLMAPIRegistry
from operator_use.inference.model.registry import ModelRegistry
from operator_use.inference.provider.registry import TextProviderRegistry
from operator_use.resource.loader import ResourceLoader
from operator_use.resource.types import ResourceLoaderOptions
from operator_use.session.manager import SessionManager
from operator_use.settings.manager import SettingsManager
from operator_use.cron.scheduler import CronScheduler as Cron
from operator_use.auth.channels import ChannelAuthManager
from operator_use.auth.acp import ACPAuthManager
from operator_use.subagent.manager import SubagentManager
from operator_use.subagent.types import SubagentSettings
from operator_use.mcp.manager import MCPManager
from operator_use.memory.manager import MemoryManager
from operator_use.memory.provider.registry import MemoryProviderRegistry
from operator_use.memory.api.registry import MemoryAPIRegistry
from operator_use.memory.types import MemoryOptions, MemoryContext
from operator_use.acp.manager import ACPSessionManager
from operator_use.peer.manager import PeerSessionManager
from operator_use.process.manager import ProcessManager
from operator_use.settings.paths import (
    get_config_dir, get_acp_auth_path, get_packages_dir,
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
    compaction_enabled: bool = True  # master on/off; ANDed with the per-strategy enabled flag
    compaction: Compaction | None = None  # supply a custom strategy (e.g. LCMCompaction)

    # Active profile — required for all resource paths (sessions, crons, acp, teams, etc.)
    profile: 'AgentProfile | None' = None


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
        peer_session_manager: PeerSessionManager | None = None,
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
        self.peer_session_manager: PeerSessionManager | None = peer_session_manager
        self.process_manager: ProcessManager | None = process_manager

    @classmethod
    async def create(
        cls,
        config: RuntimeConfig,
        settings_manager: SettingsManager | None = None,
    ) -> RuntimeContext:
        cwd = config.cwd.resolve()
        config_dir = (config.config_dir or get_config_dir()).resolve()

        # ── Bootstrap — ensure ~/.operator/ exists with default configs ───────
        from operator_use.runtime.bootstrap import bootstrap_global_dir
        bootstrap_global_dir(config_dir)

        # ── Settings ──────────────────────────────────────────────────────────
        if settings_manager is None:
            settings_manager = SettingsManager.create(cwd, config_dir)
        SettingsManager.set_instance(settings_manager)

        # ── Effective profile ─────────────────────────────────────────────────
        # Every runtime always has a profile.  Named profiles come from the
        # caller (--agent flag).  When none is provided (ephemeral REPL, base
        # gateway/ACP runtime) we create a transient in-memory profile backed
        # by a temp directory that is cleaned up on process exit.
        effective_profile: AgentProfile = config.profile or create_ephemeral_profile()

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
        resource_loader.set_active_profile(effective_profile)
        await resource_loader.reload()

        # ── Hooks ─────────────────────────────────────────────────────────────
        hooks = Hooks()
        for event_type, handler in resource_loader.get_hooks():
            hooks.register(event_type, handler)

        # ── Sandbox ───────────────────────────────────────────────────────────
        if config.sandbox and config.sandbox != 'off':
            from operator_use.sandbox import Sandbox, SandboxPolicy
            policy = (
                SandboxPolicy.strict(cwd) if config.sandbox == 'strict'
                else SandboxPolicy(mode=config.sandbox)  # type: ignore[arg-type]
            )
            Sandbox(policy, cwd).register(hooks)

        # ── Extension runtime ─────────────────────────────────────────────────
        load_result = resource_loader.get_extensions()
        extension_runtime = _DeferredExtensionRuntime(load_result)

        # ── Build inference registries ────────────────────────────────────────
        # Each registry is built from builtins then extended with whatever
        # extensions/packages registered. Registries are passed directly to
        # service constructors instead of mutating class-level globals, so
        # multiple runtimes in the same process don't stomp each other.
        from operator_use.inference.api.image.registry import ImageAPIRegistry
        from operator_use.inference.api.image.service import ImageLLM
        from operator_use.inference.provider.registry import ImageProviderRegistry, AudioProviderRegistry, VideoProviderRegistry
        from operator_use.inference.api.audio.registry import AudioAPIRegistry
        from operator_use.inference.api.audio.service import AudioLLM

        text_providers = TextProviderRegistry.from_builtins()
        text_apis = LLMAPIRegistry.from_builtins()
        text_models = ModelRegistry.from_llm_builtins()
        for _provider in extension_runtime.get_providers():
            text_providers.register(_provider)
        for _api_name, _api_cls in extension_runtime.get_text_apis().items():
            text_apis.register(_api_name, _api_cls)
        text_auth = ProviderAuthManager.create(text_providers)

        image_providers = ImageProviderRegistry.from_builtins()
        image_apis = ImageAPIRegistry.from_builtins()
        for _provider in extension_runtime.get_image_providers():
            image_providers.register(_provider)
        for _api_name, _api_cls in extension_runtime.get_image_apis().items():
            image_apis.register(_api_name, _api_cls)

        audio_providers = AudioProviderRegistry.from_builtins()
        audio_apis = AudioAPIRegistry.from_builtins()
        for _provider in extension_runtime.get_audio_providers():
            audio_providers.register(_provider)
        for _api_name, _api_cls in extension_runtime.get_audio_apis().items():
            audio_apis.register(_api_name, _api_cls)
        audio_auth = ProviderAuthManager.create(text_providers)

        try:
            from operator_use.inference.api.video.registry import VideoAPIRegistry
            from operator_use.inference.api.video.service import VideoLLM
        except ImportError:
            VideoLLM = None  # type: ignore[assignment]
            VideoAPIRegistry = None  # type: ignore[assignment]

        video_providers = VideoProviderRegistry.from_builtins()
        video_apis = VideoAPIRegistry.from_builtins() if VideoAPIRegistry is not None else None
        if VideoLLM is not None:
            for _provider in extension_runtime.get_video_providers():
                video_providers.register(_provider)
            if video_apis is not None:
                for _api_name, _api_cls in extension_runtime.get_video_apis().items():
                    video_apis.register(_api_name, _api_cls)
        video_auth = ProviderAuthManager.create(text_providers)

        # Keep class-level registries in sync so builtin hooks (stt, tts) that
        # construct AudioLLM directly still pick up extension-registered providers.
        ImageLLM._providers = image_providers
        ImageLLM._apis = image_apis
        AudioLLM._providers = audio_providers
        AudioLLM._apis = audio_apis
        AudioLLM._auth_store = audio_auth
        if VideoLLM is not None:
            VideoLLM._providers = video_providers
            if video_apis is not None:
                VideoLLM._apis = video_apis
            VideoLLM._auth_store = video_auth

        # ── LLM ───────────────────────────────────────────────────────────────
        model_id = config.model_id or settings_manager.get_default_model() or RuntimeConfig.model_fields["model_id"].default
        provider = config.provider or settings_manager.get_default_provider()
        llm = LLM(
            model_id=model_id,
            provider=provider,
            models=text_models,
            providers=text_providers,
            apis=text_apis,
            auth_store=text_auth,
        )

        # ── Compaction ────────────────────────────────────────────────────────
        # Resolve compaction settings live from the SettingsManager so that
        # changes to settings.json (e.g. {"compaction": {"enabled": false}})
        # take effect on the next check instead of being frozen at startup.
        # The RuntimeConfig values act as overrides: an explicit False/non-default
        # there still wins over the persisted setting.
        _global_enabled = config.compaction_enabled and settings_manager.get_compaction_enabled()

        # Build a dedicated LLM for compaction if auxiliary.compaction is configured.
        _aux_compaction = settings_manager.get_auxiliary_task("compaction")
        _compaction_model = _aux_compaction.model
        _compaction_provider = _aux_compaction.provider
        compaction_llm = LLM(
            model_id=_compaction_model or llm.model.id,
            provider=_compaction_provider,
            models=text_models,
            providers=text_providers,
            apis=text_apis,
            auth_store=text_auth,
        ) if (_compaction_model or _compaction_provider) else llm

        def _resolve_summarization_settings() -> CompactionSettings:
            ss = settings_manager.get_compaction_summarization_settings()
            return CompactionSettings(
                enabled=_global_enabled and ss["enabled"],
                strategy="summarization",
            )

        compaction: Compaction
        if config.compaction is not None:
            compaction = config.compaction
        else:
            _strategy = settings_manager.get_compaction_strategy()
            if _strategy == "sliding_window":
                from operator_use.compaction.strategy.sliding_window.service import SlidingWindowCompaction
                from operator_use.compaction.strategy.sliding_window.types import SlidingWindowCompactionSettings
                _rs = settings_manager.get_compaction_sliding_window_settings()
                compaction = SlidingWindowCompaction(
                    llm=compaction_llm,
                    settings=SlidingWindowCompactionSettings(
                        enabled=_global_enabled and _rs["enabled"],
                        trigger_percent=_rs["trigger_percent"],
                        batch_tokens=_rs["batch_tokens"],
                        keep_recent_tokens=_rs["keep_recent_tokens"],
                    ),
                )
            elif _strategy == "lcm":
                from operator_use.compaction.strategy.lcm.service import LCMCompaction
                from operator_use.compaction.strategy.lcm.types import LCMSettings
                from pathlib import Path as _Path
                _ls = settings_manager.get_compaction_lcm_settings()
                compaction = LCMCompaction(
                    llm=compaction_llm,
                    settings=LCMSettings(
                        enabled=_global_enabled and _ls["enabled"],
                        reserve_tokens=_ls["reserve_tokens"],
                        keep_recent_tokens=_ls["keep_recent_tokens"],
                        condense_threshold=_ls["condense_threshold"],
                        max_depth=_ls["max_depth"],
                        db_path=_Path(_ls["db_path"]) if _ls["db_path"] else None,
                    ),
                )
            else:
                compaction = SummarizationCompaction(
                    llm=compaction_llm,
                    settings=_resolve_summarization_settings(),
                    settings_provider=_resolve_summarization_settings,
                )

        # ── Session manager ───────────────────────────────────────────────────
        # Sessions are always profile-scoped.  Named profiles use their own
        # directory under profiles/<name>/sessions/.  Ephemeral profiles use a
        # tmpdir that is cleaned up on process exit, so nothing leaks to disk.
        # A custom session_dir in settings overrides the profile default for
        # named profiles only (ephemeral ones always use the tmpdir).
        if config.profile:
            _custom = settings_manager.get_session_dir()
            session_dir: Path | None = _custom if _custom is not None else effective_profile.sessions_dir
        else:
            session_dir = effective_profile.sessions_dir
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
        # Channel auth is profile-specific; no global channels.json exists.
        # Ephemeral profiles have no channels.json so auth_channel_manager is None.
        _ch_path = effective_profile.auth_channels_path if config.profile else None
        auth_channel_manager = ChannelAuthManager(_ch_path) if _ch_path else None
        acp_auth_manager = ACPAuthManager(get_acp_auth_path())
        acp_session_manager = ACPSessionManager(effective_profile.acp_dir)
        peer_session_manager = PeerSessionManager(effective_profile.peer_dir)

        # ── Compaction: inject session_id_provider and extra tools ───────────
        if hasattr(compaction, '_session_id_provider') and compaction._session_id_provider is None:  # type: ignore[union-attr]
            compaction._session_id_provider = lambda: session_manager.session_id or "default"  # type: ignore[union-attr]

        # ── Cron ─────────────────────────────────────────────────────────────
        cron: Cron | None = None
        all_tools = resource_loader.get_tools() + config.tools
        if hasattr(compaction, 'get_tools'):
            all_tools = all_tools + compaction.get_tools()  # type: ignore[union-attr]
        if settings_manager.get_cron_enabled():
            cron = Cron(store_path=effective_profile.crons_path)
            # Wire cron into the tool instance from the resource loader — the loader
            # registers the module under a different name than the package import, so
            # importing via 'from operator_use.builtins.tools.cron import tool' would give
            # a distinct object that the engine never sees.
            _cron_tool = next((t for t in all_tools if t.name == 'cron'), None)
            if _cron_tool is not None:
                _cron_tool._cron = cron  # type: ignore[attr-defined]
        else:
            all_tools = [t for t in all_tools if t.name != 'cron']

        # ── Process manager ───────────────────────────────────────────────────
        process_manager = ProcessManager(default_cwd=str(cwd), tasks_dir=effective_profile.tasks_dir)
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
                memory_manager.initialize(MemoryContext(
                    cwd=cwd,
                    session_id=session_manager.session_id or "",
                    project_memory_dir=get_config_dir() / "memory",
                    global_memory_dir=get_config_dir() / "memory",
                    llm=llm,
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
            from operator_use.builtins.tools.mcp import MCPTool
            engine.add_tool(MCPTool(
                manager=mcp_manager,
                engine=engine,
                agent_id=str(id(engine)),
            ))

        # ── ACP agent tool ─────────────────────────────────────────────────────
        from operator_use.builtins.tools.acp_agent import ACPAgentTool
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
            memory_manager=memory_manager,
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
            peer_session_manager=peer_session_manager,
            process_manager=process_manager,
        )


class _DeferredExtensionRuntime(ExtensionRuntime):
    """No-op runtime used before the real context (Agent) is available."""

    def __init__(self, load_result: LoadExtensionsResult) -> None:
        class _NullCtx:
            pass
        super().__init__(load_result, _NullCtx())  # type: ignore[arg-type]

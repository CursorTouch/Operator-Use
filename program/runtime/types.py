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
from program.acp.manager import ACPSessionManager
from program.settings.paths import get_config_dir, get_crons_path, get_channels_auth_path, get_auth_path, get_acp_sessions_dir


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
        auth_manager: ChannelAuthManager | None = None,
        subagent_settings: SubagentSettings | None = None,
        mcp_manager: MCPManager | None = None,
        acp_auth: ACPAuthManager | None = None,
        acp_manager: ACPSessionManager | None = None,
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
        self.auth_manager = auth_manager
        self.subagent_settings = subagent_settings or SubagentSettings()
        self.mcp_manager: MCPManager | None = mcp_manager
        self.acp_auth: ACPAuthManager | None = acp_auth
        self.acp_manager: ACPSessionManager | None = acp_manager

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

        # ── LLM ───────────────────────────────────────────────────────────────
        model_id = config.model_id or settings_manager.get_default_model()
        provider = config.provider or settings_manager.get_default_provider()
        llm = LLM(model_id=model_id, provider=provider)

        # ── Resource loader ───────────────────────────────────────────────────
        loader_opts = ResourceLoaderOptions(
            cwd=cwd,
            config_dir=config_dir,
            no_extensions=config.no_extensions,
            no_skills=config.no_skills,
            no_context_files=config.no_context_files,
            system_prompt=config.system_prompt,
            append_system_prompt=config.append_system_prompt,
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
        session_manager = SessionManager(
            cwd=cwd,
            session_dir=session_dir,
            session_file=config.session_file,
            persist=config.persist_session,
        )

        # ── Auth ─────────────────────────────────────────────────────────────
        auth_manager = ChannelAuthManager(get_channels_auth_path())
        acp_auth = ACPAuthManager(get_auth_path())
        acp_manager = ACPSessionManager(get_acp_sessions_dir())

        # ── Cron ─────────────────────────────────────────────────────────────
        from program.builtins.tools.cron import tool as cron_tool
        cron: Cron | None = None
        all_tools = resource_loader.get_tools() + config.tools
        if settings_manager.get_cron_enabled():
            cron = Cron(store_path=get_crons_path(config_dir))
            cron_tool._cron = cron
        else:
            all_tools = [t for t in all_tools if t.name != 'cron']

        # ── MCP ───────────────────────────────────────────────────────────────
        mcp_configs = settings_manager.get_mcp_servers()
        mcp_manager: MCPManager | None = MCPManager(mcp_configs) if mcp_configs else None

        # ── Agent loop ────────────────────────────────────────────────────────
        engine = Engine(
            llm=llm,
            tools=all_tools,
            options=Options(),
            hooks=hooks,
        )

        # ── MCP builtin tool (per-engine, so the agent can manage connections) ─
        if mcp_manager is not None:
            from program.builtins.tools.mcp import MCPBuiltinTool
            engine.add_tool(MCPBuiltinTool(
                manager=mcp_manager,
                engine=engine,
                agent_id=str(id(engine)),
            ))

        # ── ACP agent tool ─────────────────────────────────────────────────────
        acp_agent_configs = settings_manager.get_acp_agents()
        if acp_agent_configs:
            from program.builtins.tools.acp_agent import ACPAgentTool
            engine.add_tool(ACPAgentTool(
                registry=acp_agent_configs,
                session_manager=acp_manager,
                auth_manager=acp_auth,
                bus=None,   # bus not yet available; Runtime.create() re-wires after gateway starts
                agent=None,
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
            auth_manager=auth_manager,
            subagent_settings=SubagentSettings(),
            mcp_manager=mcp_manager,
            acp_auth=acp_auth,
            acp_manager=acp_manager,
        )


class _DeferredExtensionRuntime(ExtensionRuntime):
    """No-op runtime used before the real context (Agent) is available."""

    def __init__(self, load_result: LoadExtensionsResult) -> None:
        class _NullCtx:
            pass
        super().__init__(load_result, _NullCtx())  # type: ignore[arg-type]

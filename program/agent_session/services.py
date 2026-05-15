from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from program.agent_session.session import AgentSession
from program.agent_session.types import AgentSessionConfig
from program.compaction.compact import Compaction
from program.compaction.types import CompactionSettings
from program.engine.loop import AgentLoop
from program.engine.types import Options
from program.extension.loader import discover_and_load_extensions
from program.extension.runtime import ExtensionRuntime
from program.extension.types import LoadExtensionsResult
from program.llm.service import LLM
from program.resource.loader import DefaultResourceLoader
from program.resource.types import ResourceLoaderOptions
from program.session.manager import SessionManager
from program.settings.manager import SettingsManager


# ============================================================================
# Configuration for service creation
# ============================================================================

class AgentSessionServicesConfig(BaseModel):
    model_config = {'arbitrary_types_allowed': True}

    cwd: Path
    agent_dir: Path | None = None

    # LLM
    model_id: str = 'claude-sonnet-4-6'
    provider: str | None = None

    # Session
    session_file: Path | None = None
    persist_session: bool = True

    # Tools & prompt
    tools: list[Any] = Field(default_factory=list)     # list[Tool]
    selected_tools: list[str] | None = None
    tool_snippets: dict[str, str] = Field(default_factory=dict)
    prompt_guidelines: list[str] = Field(default_factory=list)

    # Resource loader
    no_extensions: bool = False
    no_skills: bool = False
    no_context_files: bool = False
    system_prompt: str | None = None
    append_system_prompt: list[str] = Field(default_factory=list)

    # Compaction
    compaction_enabled: bool = True
    compaction_reserve_tokens: int = 16384
    compaction_keep_recent_tokens: int = 20000


# ============================================================================
# Service bundle
# ============================================================================

class AgentSessionServices:
    """
    Constructs and owns all dependencies for one AgentSession.

    Usage:
        services = await AgentSessionServices.create(config)
        session = services.session
        await session.prompt("hello")
    """

    def __init__(
        self,
        session: AgentSession,
        llm: LLM,
        loop: AgentLoop,
        session_manager: SessionManager,
        resource_loader: DefaultResourceLoader,
        extension_runtime: ExtensionRuntime,
        compaction: Compaction,
        settings_manager: SettingsManager | None,
    ) -> None:
        self.session = session
        self.llm = llm
        self.loop = loop
        self.session_manager = session_manager
        self.resource_loader = resource_loader
        self.extension_runtime = extension_runtime
        self.compaction = compaction
        self.settings_manager = settings_manager

    @classmethod
    async def create(
        cls,
        config: AgentSessionServicesConfig,
        settings_manager: SettingsManager | None = None,
    ) -> AgentSessionServices:
        cwd = config.cwd.resolve()
        agent_dir = (config.agent_dir or Path.home() / '.program').resolve()

        # ── Settings ──────────────────────────────────────────────────────────
        if settings_manager is None:
            settings_manager = SettingsManager.create(cwd, agent_dir)

        # ── LLM ───────────────────────────────────────────────────────────────
        model_id = config.model_id or settings_manager.get_default_model() or 'claude-sonnet-4-6'
        provider = config.provider or settings_manager.get_default_provider()
        llm = LLM(model_id=model_id, provider=provider)

        # ── Resource loader ───────────────────────────────────────────────────
        loader_opts = ResourceLoaderOptions(
            cwd=cwd,
            agent_dir=agent_dir,
            no_extensions=config.no_extensions,
            no_skills=config.no_skills,
            no_context_files=config.no_context_files,
            system_prompt=config.system_prompt,
            append_system_prompt=config.append_system_prompt,
        )
        resource_loader = DefaultResourceLoader(loader_opts)
        await resource_loader.reload()

        # ── Extension runtime ─────────────────────────────────────────────────
        load_result = resource_loader.get_extensions()
        # Placeholder context — replaced once AgentSession is created
        extension_runtime = _DeferredExtensionRuntime(load_result)

        # ── Compaction ────────────────────────────────────────────────────────
        compaction_settings = CompactionSettings(
            enabled=config.compaction_enabled,
            reserve_tokens=config.compaction_reserve_tokens,
            keep_recent_tokens=config.compaction_keep_recent_tokens,
        )
        compaction = Compaction(llm=llm, settings=compaction_settings)

        # ── Session manager ───────────────────────────────────────────────────
        session_dir = settings_manager.get_session_dir()
        session_manager = SessionManager(
            cwd=cwd,
            session_dir=session_dir,
            session_file=config.session_file,
            persist=config.persist_session,
        )

        # ── Agent loop ────────────────────────────────────────────────────────
        loop = AgentLoop(
            llm=llm,
            tools=config.tools,
            options=Options(),
        )

        # ── Agent session config ──────────────────────────────────────────────
        session_config = AgentSessionConfig(
            cwd=cwd,
            model=llm.model,
            context_window=llm.model.context_window or 200_000,
            selected_tools=config.selected_tools,
            tool_snippets=config.tool_snippets,
            prompt_guidelines=config.prompt_guidelines,
            retry_enabled=settings_manager.get_retry_enabled(),
            retry_max_retries=settings_manager.get_retry_max_retries(),
            retry_base_delay_ms=settings_manager.get_retry_base_delay_ms(),
        )

        # ── Wire everything together ──────────────────────────────────────────
        # Build a real ExtensionRuntime now that we have the session as context
        agent_session = AgentSession(
            loop=loop,
            session_manager=session_manager,
            resource_loader=resource_loader,
            extension_runtime=extension_runtime,  # type: ignore[arg-type]
            compaction=compaction,
            config=session_config,
        )

        # Now replace with a real runtime pointing at the session as context
        real_runtime = ExtensionRuntime(load_result, agent_session)
        agent_session._extensions = real_runtime

        return cls(
            session=agent_session,
            llm=llm,
            loop=loop,
            session_manager=session_manager,
            resource_loader=resource_loader,
            extension_runtime=real_runtime,
            compaction=compaction,
            settings_manager=settings_manager,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Internal placeholder used during two-phase construction
# ──────────────────────────────────────────────────────────────────────────────

class _DeferredExtensionRuntime(ExtensionRuntime):
    """No-op runtime used before the real context (AgentSession) is available."""

    def __init__(self, load_result: LoadExtensionsResult) -> None:
        # Pass a minimal stub context; replaced immediately after construction
        class _NullCtx:
            pass
        super().__init__(load_result, _NullCtx())  # type: ignore[arg-type]

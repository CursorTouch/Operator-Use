from __future__ import annotations

from typing import TYPE_CHECKING

from program.runtime.session import AgentSession
from program.runtime.types import AgentSessionConfig, SessionConfig
from program.compaction.compact import Compaction
from program.compaction.types import CompactionSettings
from program.engine.loop import AgentLoop
from program.engine.types import Options
from program.extension.loader import discover_and_load_extensions
from program.extension.runtime import ExtensionRuntime
from program.extension.types import LoadExtensionsResult
from program.hooks.service import Hooks
from program.inference.api.llm.service import LLM
from program.resource.loader import ResourceLoader
from program.resource.types import ResourceLoaderOptions
from program.session.manager import SessionManager
from program.settings.manager import SettingsManager
from program.settings.paths import get_config_dir

if TYPE_CHECKING:
    pass


class AgentSessionLoader:
    """
    Constructs and owns all dependencies for one AgentSession.

    Usage:
        loader = await AgentSessionLoader.create(config)
        session = loader.session
        await session.prompt("hello")
    """

    def __init__(
        self,
        session: AgentSession,
        llm: LLM,
        loop: AgentLoop,
        session_manager: SessionManager,
        resource_loader: ResourceLoader,
        extension_runtime: ExtensionRuntime,
        compaction: Compaction,
        settings_manager: SettingsManager | None,
        hooks: Hooks | None = None,
    ) -> None:
        self.session = session
        self.llm = llm
        self.loop = loop
        self.session_manager = session_manager
        self.resource_loader = resource_loader
        self.extension_runtime = extension_runtime
        self.compaction = compaction
        self.settings_manager = settings_manager
        self.hooks: Hooks = hooks or extension_runtime._hooks

    @classmethod
    async def create(
        cls,
        config: AgentSessionConfig,
        settings_manager: SettingsManager | None = None,
    ) -> AgentSessionLoader:
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
            hooks=hooks,
        )

        # ── Session config ────────────────────────────────────────────────────
        session_config = SessionConfig(
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
        agent_session = AgentSession(
            loop=loop,
            session_manager=session_manager,
            resource_loader=resource_loader,
            extension_runtime=extension_runtime,  # type: ignore[arg-type]
            compaction=compaction,
            config=session_config,
        )

        # Replace placeholder with real runtime pointing at the session as context
        real_runtime = ExtensionRuntime(load_result, agent_session, hooks=hooks)
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
            hooks=hooks,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Internal placeholder used during two-phase construction
# ──────────────────────────────────────────────────────────────────────────────

class _DeferredExtensionRuntime(ExtensionRuntime):
    """No-op runtime used before the real context (AgentSession) is available."""

    def __init__(self, load_result: LoadExtensionsResult) -> None:
        class _NullCtx:
            pass
        super().__init__(load_result, _NullCtx())  # type: ignore[arg-type]

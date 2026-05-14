from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from program.auth.manager import AuthManager
from program.llm.model.registry import ModelRegistry
from program.resource.loader import DefaultResourceLoader, DefaultResourceLoaderOptions
from program.session.manager import SessionManager
from program.settings.manager import SettingsManager

if TYPE_CHECKING:
    from program.extensions.types import ToolDefinition


@dataclass
class AgentSessionRuntimeDiagnostic:
    type: str  # "info" | "warning" | "error"
    message: str


@dataclass
class AgentSessionServices:
    cwd: str
    agent_dir: str
    auth_manager: AuthManager
    settings_manager: SettingsManager
    model_registry: ModelRegistry
    resource_loader: DefaultResourceLoader
    diagnostics: list[AgentSessionRuntimeDiagnostic] = field(default_factory=list)


@dataclass
class CreateAgentSessionServicesOptions:
    cwd: str
    agent_dir: Optional[str] = None
    auth_manager: Optional[AuthManager] = None
    settings_manager: Optional[SettingsManager] = None
    model_registry: Optional[ModelRegistry] = None
    extension_flag_values: Optional[dict[str, bool | str]] = None
    resource_loader_options: Optional[dict] = None


@dataclass
class CreateAgentSessionFromServicesOptions:
    services: AgentSessionServices
    session_manager: SessionManager
    session_start_event: Optional[dict] = None
    model: Optional[Any] = None
    thinking_level: Optional[str] = None
    scoped_models: Optional[list] = None
    tools: Optional[list[str]] = None
    no_tools: Optional[Any] = None
    custom_tools: Optional[list] = None


def _get_default_agent_dir() -> str:
    return str(Path.home() / ".program")


def _apply_extension_flag_values(
    resource_loader: DefaultResourceLoader,
    extension_flag_values: Optional[dict[str, bool | str]],
) -> list[AgentSessionRuntimeDiagnostic]:
    if not extension_flag_values:
        return []

    diagnostics: list[AgentSessionRuntimeDiagnostic] = []
    extensions_result = resource_loader.get_extensions()
    registered_flags: dict[str, Any] = {}
    for extension in extensions_result.extensions:
        for name, flag in extension.flags.items():
            registered_flags[name] = flag

    unknown_flags: list[str] = []
    for name, value in extension_flag_values.items():
        flag = registered_flags.get(name)
        if not flag:
            unknown_flags.append(name)
            continue
        if flag.type == "boolean":
            extensions_result.runtime.flag_values[name] = True
        elif isinstance(value, str):
            extensions_result.runtime.flag_values[name] = value
        else:
            diagnostics.append(AgentSessionRuntimeDiagnostic(
                type="error",
                message=f'Extension flag "--{name}" requires a value',
            ))

    if unknown_flags:
        s = "s" if len(unknown_flags) > 1 else ""
        names = ", ".join(f"--{n}" for n in unknown_flags)
        diagnostics.append(AgentSessionRuntimeDiagnostic(
            type="error",
            message=f"Unknown option{s}: {names}",
        ))

    return diagnostics


async def create_agent_session_services(
    options: CreateAgentSessionServicesOptions,
) -> AgentSessionServices:
    cwd = options.cwd
    agent_dir = options.agent_dir or _get_default_agent_dir()

    auth_manager = options.auth_manager or AuthManager.create(None, Path(agent_dir) / "auth.json")
    settings_manager = options.settings_manager or SettingsManager.create(Path(cwd), Path(agent_dir))
    model_registry = options.model_registry or ModelRegistry.from_builtin()

    loader_kwargs = dict(options.resource_loader_options or {})
    resource_loader = DefaultResourceLoader(DefaultResourceLoaderOptions(
        **loader_kwargs,
        cwd=cwd,
        agent_dir=agent_dir,
        settings_manager=settings_manager,
    ))
    await resource_loader.reload()

    diagnostics: list[AgentSessionRuntimeDiagnostic] = []

    extensions_result = resource_loader.get_extensions()
    extensions_result.runtime.pending_provider_registrations = []

    diagnostics.extend(_apply_extension_flag_values(resource_loader, options.extension_flag_values))

    return AgentSessionServices(
        cwd=cwd,
        agent_dir=agent_dir,
        auth_manager=auth_manager,
        settings_manager=settings_manager,
        model_registry=model_registry,
        resource_loader=resource_loader,
        diagnostics=diagnostics,
    )


async def create_agent_session_from_services(
    options: CreateAgentSessionFromServicesOptions,
) -> Any:
    from program.agent.session import AgentSession, AgentSessionConfig

    return AgentSession(AgentSessionConfig(
        cwd=options.services.cwd,
        agent_dir=options.services.agent_dir,
        auth_manager=options.services.auth_manager,
        settings_manager=options.services.settings_manager,
        model_registry=options.services.model_registry,
        resource_loader=options.services.resource_loader,
        session_manager=options.session_manager,
        model=options.model,
        thinking_level=options.thinking_level,
        scoped_models=options.scoped_models or [],
        custom_tools=options.custom_tools or [],
        initial_active_tool_names=options.tools,
        session_start_event=options.session_start_event,
    ))

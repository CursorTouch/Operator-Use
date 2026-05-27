from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from operator_use.diagnostics.types import ResourceDiagnostic
    from operator_use.extension.runtime import ExtensionRuntime
    from operator_use.skill.types import Skill
    from operator_use.extension.types import LoadExtensionsResult
    from operator_use.subagent.profile import SubagentProfile
    from operator_use.agent.profile import AgentProfile


# ============================================================================
# Context files
# ============================================================================

class ContextFile(BaseModel):
    path: str
    content: str


# ============================================================================
# Extension resource paths (provided via resources_discover event)
# ============================================================================

class ResourceExtensionPaths(BaseModel):
    skill_paths: list[str] = Field(default_factory=list)


# ============================================================================
# Resource loader interface
# ============================================================================

class BaseResourceLoader(ABC):
    @abstractmethod
    def get_extensions(self) -> 'LoadExtensionsResult': ...

    @abstractmethod
    def get_skills(self) -> tuple[list['Skill'], list['ResourceDiagnostic']]: ...

    @abstractmethod
    def get_tools(self) -> list: ...

    @abstractmethod
    def get_commands(self) -> list: ...

    @abstractmethod
    def get_hooks(self) -> list: ...

    @abstractmethod
    def get_context_files(self) -> list[ContextFile]: ...

    @abstractmethod
    def get_system_prompt(self) -> str | None: ...

    @abstractmethod
    def get_append_system_prompt(self) -> list[str]: ...

    @abstractmethod
    def get_soul_prompt(self) -> str | None: ...

    @abstractmethod
    def get_user_profile(self) -> str | None: ...

    @abstractmethod
    def get_agent_memory(self) -> str | None: ...

    @abstractmethod
    def extend_resources(self, paths: ResourceExtensionPaths) -> None: ...

    @abstractmethod
    async def reload(self) -> None: ...

    @abstractmethod
    def get_subagent_profiles(self) -> list['SubagentProfile']: ...

    @abstractmethod
    def get_agent_profiles(self) -> list['AgentProfile']: ...

    @abstractmethod
    def set_active_profile(self, profile: 'AgentProfile | None') -> None: ...

    @abstractmethod
    def get_diagnostics(self, runtime: 'ExtensionRuntime | None' = None) -> list['ResourceDiagnostic']:
        """Return all diagnostics: extension errors, collisions, and skill warnings."""
        ...


# ============================================================================
# Options
# ============================================================================

class ResourceLoaderOptions(BaseModel):
    model_config = {'arbitrary_types_allowed': True}

    cwd: Path
    config_dir: Path
    additional_extension_dirs: list[Path] = Field(default_factory=list)
    additional_skill_paths: list[str] = Field(default_factory=list)
    additional_tool_dirs: list[Path] = Field(default_factory=list)
    no_extensions: bool = False
    no_skills: bool = False
    no_context_files: bool = False
    system_prompt: str | None = None
    append_system_prompt: list[str] = Field(default_factory=list)
    disabled_extension_stems: set[str] = Field(default_factory=set)
    extension_configs: dict[str, dict] = Field(default_factory=dict)
    package_sources: list[str] = Field(default_factory=list)
    packages_dir: Path | None = None

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from program.diagnostics.types import ResourceDiagnostic
    from program.extension.runtime import ExtensionRuntime
    from program.skill.types import Skill
    from program.extension.types import LoadExtensionsResult


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
    def extend_resources(self, paths: ResourceExtensionPaths) -> None: ...

    @abstractmethod
    async def reload(self) -> None: ...

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

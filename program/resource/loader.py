from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from program.bus.service import EventBus
from program.diagnostics.service import run_diagnostics
from program.diagnostics.types import ResourceDiagnostic
from program.extension.loader import discover_and_load_extensions
from program.extension.types import LoadExtensionsResult
from program.resource.context import load_project_context_files
from program.resource.types import ContextFile, ResourceExtensionPaths, BaseResourceLoader, ResourceLoaderOptions
from program.skill.loader import load_skills
from program.skill.types import Skill, LoadSkillsOptions

if TYPE_CHECKING:
    from program.extension.runtime import ExtensionRuntime

_SYSTEM_PROMPT_FILE = "SYSTEM.md"
_APPEND_SYSTEM_PROMPT_FILE = "APPEND_SYSTEM.md"
_PROJECT_CONFIG_DIR = ".program"


def _read_optional_file(path: Path) -> str | None:
    try:
        return path.read_text(encoding='utf-8') if path.is_file() else None
    except OSError:
        return None


def _discover_system_prompt(cwd: Path, agent_dir: Path) -> str | None:
    project = cwd / _PROJECT_CONFIG_DIR / _SYSTEM_PROMPT_FILE
    if project.is_file():
        return _read_optional_file(project)
    global_ = agent_dir / _SYSTEM_PROMPT_FILE
    return _read_optional_file(global_)


def _discover_append_system_prompt(cwd: Path, agent_dir: Path) -> str | None:
    project = cwd / _PROJECT_CONFIG_DIR / _APPEND_SYSTEM_PROMPT_FILE
    if project.is_file():
        return _read_optional_file(project)
    global_ = agent_dir / _APPEND_SYSTEM_PROMPT_FILE
    return _read_optional_file(global_)


class ResourceLoader(BaseResourceLoader):
    """
    Discovers and caches skills, extensions, and context files for one cwd.
    Call reload() once before use. reload() is also called to refresh after settings change.
    """

    def __init__(self, options: ResourceLoaderOptions) -> None:
        self._cwd = options.cwd
        self._agent_dir = options.agent_dir
        self._additional_extension_dirs = options.additional_extension_dirs
        self._additional_skill_paths = options.additional_skill_paths
        self._no_extensions = options.no_extensions
        self._no_skills = options.no_skills
        self._no_context_files = options.no_context_files
        self._system_prompt_override = options.system_prompt
        self._append_system_prompt_override = options.append_system_prompt

        self._bus = EventBus()

        # Cached state (populated by reload)
        self._extensions_result: LoadExtensionsResult = LoadExtensionsResult()
        self._skills: list[Skill] = []
        self._skill_diagnostics: list[ResourceDiagnostic] = []
        self._context_files: list[ContextFile] = []
        self._system_prompt: str | None = None
        self._append_system_prompt: list[str] = []
        self._extension_skill_paths: list[str] = []

    # -------------------------------------------------------------------------
    # Public interface
    # -------------------------------------------------------------------------

    def get_extensions(self) -> LoadExtensionsResult:
        return self._extensions_result

    def get_skills(self) -> tuple[list[Skill], list[ResourceDiagnostic]]:
        return self._skills, self._skill_diagnostics

    def get_context_files(self) -> list[ContextFile]:
        return self._context_files

    def get_system_prompt(self) -> str | None:
        return self._system_prompt

    def get_append_system_prompt(self) -> list[str]:
        return self._append_system_prompt

    def extend_resources(self, paths: ResourceExtensionPaths) -> None:
        """Called after resources_discover to add extension-provided skill paths."""
        new_paths = [p for p in paths.skill_paths if p not in self._extension_skill_paths]
        if not new_paths:
            return
        self._extension_skill_paths.extend(new_paths)
        self._reload_skills()

    def get_diagnostics(self, runtime: ExtensionRuntime | None = None) -> list[ResourceDiagnostic]:
        """Return all diagnostics: extension load errors, collisions, skill warnings, and (optionally) runtime errors."""
        from program.skill.types import LoadSkillsResult
        skills_result = LoadSkillsResult(skills=self._skills, diagnostics=self._skill_diagnostics)
        return run_diagnostics(self._extensions_result, skills_result=skills_result, runtime=runtime)

    async def reload(self) -> None:
        await self._reload_extensions()
        self._reload_skills()
        self._reload_context_files()
        self._reload_system_prompt()

    # -------------------------------------------------------------------------
    # Internal reload helpers
    # -------------------------------------------------------------------------

    async def _reload_extensions(self) -> None:
        if self._no_extensions:
            self._extensions_result = LoadExtensionsResult()
            return

        dirs: list[Path] = []
        # Default: project-local and agent-global extension dirs
        project_ext = self._cwd / _PROJECT_CONFIG_DIR / 'extensions'
        agent_ext = self._agent_dir / 'extensions'
        if project_ext.is_dir():
            dirs.append(project_ext)
        if agent_ext.is_dir():
            dirs.append(agent_ext)
        dirs.extend(self._additional_extension_dirs)

        self._extensions_result = await discover_and_load_extensions(dirs, self._bus)

    def _reload_skills(self) -> None:
        if self._no_skills:
            self._skills = []
            self._skill_diagnostics = []
            return

        all_skill_paths = list(self._additional_skill_paths) + list(self._extension_skill_paths)
        result = load_skills(LoadSkillsOptions(
            cwd=self._cwd,
            agent_dir=self._agent_dir,
            skill_paths=all_skill_paths,
            include_defaults=True,
        ))
        self._skills = result.skills
        self._skill_diagnostics = result.diagnostics

    def _reload_context_files(self) -> None:
        if self._no_context_files:
            self._context_files = []
            return
        self._context_files = load_project_context_files(self._cwd, self._agent_dir)

    def _reload_system_prompt(self) -> None:
        if self._system_prompt_override is not None:
            self._system_prompt = self._system_prompt_override
        else:
            self._system_prompt = _discover_system_prompt(self._cwd, self._agent_dir)

        discovered_append = _discover_append_system_prompt(self._cwd, self._agent_dir)
        if self._append_system_prompt_override:
            self._append_system_prompt = list(self._append_system_prompt_override)
        elif discovered_append:
            self._append_system_prompt = [discovered_append]
        else:
            self._append_system_prompt = []

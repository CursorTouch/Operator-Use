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
from program.tool.loader import load_tools
from program.tool.types import Tool
from program.commands.loader import load_commands
from program.commands.types import SlashCommandInfo
from program.hooks.loader import load_hooks, HookRegistration
from program.package.loader import load_packages_from_settings
from program.settings.paths import (
    get_extensions_dir, get_skills_dir, get_tools_dir, get_commands_dir, get_hooks_dir,
    get_system_prompt_path, get_append_system_prompt_path, get_knowledge_dir, get_temp_dir,
    get_builtins_commands_dir, get_builtins_tools_dir, get_builtins_skills_dir,
    get_builtins_extensions_dir, get_builtins_hooks_dir,
)
from program.skill.loader import load_skills
from program.skill.types import Skill, LoadSkillsOptions

if TYPE_CHECKING:
    from program.extension.runtime import ExtensionRuntime


def _read_optional_file(path: Path) -> str | None:
    try:
        return path.read_text(encoding='utf-8') if path.is_file() else None
    except OSError:
        return None


def _discover_system_prompt(cwd: Path) -> str | None:
    project = get_system_prompt_path(cwd)
    if project.is_file():
        return _read_optional_file(project)
    return _read_optional_file(get_system_prompt_path())


def _discover_append_system_prompt(cwd: Path) -> str | None:
    project = get_append_system_prompt_path(cwd)
    if project.is_file():
        return _read_optional_file(project)
    return _read_optional_file(get_append_system_prompt_path())


class ResourceLoader(BaseResourceLoader):
    """
    Discovers and caches skills, extensions, and context files for one cwd.
    Call reload() once before use. reload() is also called to refresh after settings change.
    """

    def __init__(self, options: ResourceLoaderOptions) -> None:
        self._cwd = options.cwd
        self._config_dir = options.config_dir
        self._additional_extension_dirs = options.additional_extension_dirs
        self._additional_skill_paths = options.additional_skill_paths
        self._additional_tool_dirs = options.additional_tool_dirs
        self._no_extensions = options.no_extensions
        self._no_skills = options.no_skills
        self._no_context_files = options.no_context_files
        self._system_prompt_override = options.system_prompt
        self._append_system_prompt_override = options.append_system_prompt
        self._disabled_extension_stems = options.disabled_extension_stems
        self._extension_configs = options.extension_configs
        self._package_sources = options.package_sources
        self._packages_dir = options.packages_dir

        self._bus = EventBus()

        # Cached state (populated by reload)
        self._extensions_result: LoadExtensionsResult = LoadExtensionsResult()
        self._skills: list[Skill] = []
        self._skill_diagnostics: list[ResourceDiagnostic] = []
        self._tools: list[Tool] = []
        self._commands: list[SlashCommandInfo] = []
        self._hooks: list[HookRegistration] = []
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

    def get_tools(self) -> list[Tool]:
        return self._tools

    def get_commands(self) -> list[SlashCommandInfo]:
        return self._commands

    def get_hooks(self) -> list[HookRegistration]:
        return self._hooks

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
        self._reload_tools()
        self._reload_commands()
        self._reload_hooks()
        self._reload_context_files()
        self._reload_system_prompt()

    # -------------------------------------------------------------------------
    # Internal reload helpers
    # -------------------------------------------------------------------------

    async def _reload_extensions(self) -> None:
        if self._no_extensions:
            self._extensions_result = LoadExtensionsResult()
            return

        dirs: list[Path] = [get_builtins_extensions_dir()]
        project_ext = get_extensions_dir(self._cwd)
        global_ext = get_extensions_dir()
        if project_ext.is_dir():
            dirs.append(project_ext)
        if global_ext.is_dir():
            dirs.append(global_ext)
        dirs.extend(self._additional_extension_dirs)

        if self._package_sources and self._packages_dir:
            loaded = load_packages_from_settings(self._package_sources, self._packages_dir, self._cwd)
            self._package_skill_paths = loaded.skill_paths
            dirs.extend(loaded.extension_dirs)
        else:
            self._package_skill_paths: list[str] = []

        self._extensions_result = await discover_and_load_extensions(
            dirs, self._bus,
            disabled_stems=self._disabled_extension_stems or None,
            entry_configs=self._extension_configs or None,
        )

    def _reload_skills(self) -> None:
        if self._no_skills:
            self._skills = []
            self._skill_diagnostics = []
            return

        all_skill_paths = (
            [str(get_builtins_skills_dir())]
            + list(self._additional_skill_paths)
            + list(self._extension_skill_paths)
            + getattr(self, "_package_skill_paths", [])
        )
        result = load_skills(LoadSkillsOptions(
            cwd=self._cwd,
            skill_paths=all_skill_paths,
            include_defaults=True,
        ))
        self._skills = result.skills
        self._skill_diagnostics = result.diagnostics

    def _reload_tools(self) -> None:
        dirs = [get_builtins_tools_dir()]
        project_tools = get_tools_dir(self._cwd)
        global_tools = get_tools_dir()
        if project_tools.is_dir():
            dirs.append(project_tools)
        if global_tools.is_dir():
            dirs.append(global_tools)
        dirs.extend(self._additional_tool_dirs)
        self._tools = load_tools(dirs).tools

    def _reload_commands(self) -> None:
        dirs = [get_builtins_commands_dir()]
        project_cmds = get_commands_dir(self._cwd)
        global_cmds = get_commands_dir()
        if project_cmds.is_dir():
            dirs.append(project_cmds)
        if global_cmds.is_dir():
            dirs.append(global_cmds)
        self._commands = load_commands(dirs).commands

    def _reload_hooks(self) -> None:
        dirs = [get_builtins_hooks_dir()]
        project_hooks = get_hooks_dir(self._cwd)
        global_hooks = get_hooks_dir()
        if project_hooks.is_dir():
            dirs.append(project_hooks)
        if global_hooks.is_dir():
            dirs.append(global_hooks)
        self._hooks = load_hooks(dirs).hooks

    def _reload_context_files(self) -> None:
        if self._no_context_files:
            self._context_files = []
            return
        self._context_files = load_project_context_files(self._cwd, self._config_dir)

    def _reload_system_prompt(self) -> None:
        if self._system_prompt_override is not None:
            self._system_prompt = self._system_prompt_override
        else:
            self._system_prompt = _discover_system_prompt(self._cwd)

        discovered_append = _discover_append_system_prompt(self._cwd)
        if self._append_system_prompt_override:
            self._append_system_prompt = list(self._append_system_prompt_override)
        elif discovered_append:
            self._append_system_prompt = [discovered_append]
        else:
            self._append_system_prompt = []

        # Append knowledge index if any docs exist (project-level takes precedence)
        from program.knowledge.service import Knowledge
        knowledge = Knowledge(get_knowledge_dir(self._cwd), get_knowledge_dir())
        if index := knowledge.build_knowledge_index():
            self._append_system_prompt.append(index)

        # Ensure temp dirs exist so the agent can use them immediately
        get_temp_dir().mkdir(parents=True, exist_ok=True)
        get_temp_dir(self._cwd).mkdir(parents=True, exist_ok=True)

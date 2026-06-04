from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from operator_use.bus.service import EventBus
from operator_use.diagnostics.service import run_diagnostics
from operator_use.diagnostics.types import ResourceDiagnostic
from operator_use.extension.loader import discover_and_load_extensions
from operator_use.extension.types import LoadExtensionsResult
from operator_use.resource.types import ResourceExtensionPaths, BaseResourceLoader, ResourceLoaderOptions
from operator_use.tool.loader import load_tools
from operator_use.tool.types import Tool
from operator_use.commands.loader import load_commands
from operator_use.commands.types import SlashCommandInfo
from operator_use.hooks.loader import load_hooks, HookRegistration
from operator_use.package.loader import load_packages_from_settings
from operator_use.settings.paths import (
    CONFIG_DIR_NAME,
    get_builtins_commands_dir, get_builtins_tools_dir, get_builtins_skills_dir,
    get_builtins_extensions_dir, get_builtins_hooks_dir, get_builtins_subagents_dir,
    get_profiles_dir,
)
from operator_use.skill.loader import load_skills
from operator_use.skill.types import Skill, LoadSkillsOptions
from operator_use.subagent.profile import SubagentProfile, load_profiles
from operator_use.agent.profile import AgentProfile, load_agent_profiles

if TYPE_CHECKING:
    from operator_use.extension.runtime import ExtensionRuntime


def _read_optional_file(path: Path) -> str | None:
    """Read a file, returning None if not found or on read error."""
    try:
        return path.read_text(encoding='utf-8') if path.is_file() else None
    except OSError:
        return None



class ResourceLoader(BaseResourceLoader):
    """
    Discovers and caches skills, extensions, and context files.
    Call reload() once before use. reload() is also called to refresh after settings change.

    When a profile is active (set via set_active_profile()), profile-specific resource
    directories replace the global user directories. Auth always comes from global.
    """

    def __init__(self, options: ResourceLoaderOptions) -> None:
        self._cwd = options.cwd
        self._config_dir = options.config_dir
        self._additional_extension_dirs = options.additional_extension_dirs
        self._additional_skill_paths = options.additional_skill_paths
        self._additional_tool_dirs = options.additional_tool_dirs
        self._no_extensions = options.no_extensions
        self._no_skills = options.no_skills
        self._system_prompt_override = options.system_prompt
        self._append_system_prompt_override = options.append_system_prompt
        self._disabled_extension_stems = options.disabled_extension_stems
        self._extension_configs = options.extension_configs
        self._package_sources = options.package_sources
        self._packages_dir = options.packages_dir

        self._bus = EventBus()
        self._active_profile: AgentProfile | None = None

        # Cached state (populated by reload)
        self._extensions_result: LoadExtensionsResult = LoadExtensionsResult()
        self._skills: list[Skill] = []
        self._skill_diagnostics: list[ResourceDiagnostic] = []
        self._tools: list[Tool] = []
        self._commands: list[SlashCommandInfo] = []
        self._hooks: list[HookRegistration] = []
        self._system_prompt: str | None = None
        self._append_system_prompt: list[str] = []
        self._soul_prompt: str | None = None
        self._user_profile: str | None = None
        self._agent_memory: str | None = None
        self._tools_reference: str | None = None
        self._extension_skill_paths: list[str] = []
        self._extension_workflow_paths: list[str] = []
        self._subagent_profiles: list[SubagentProfile] = []
        self._agent_profiles: list[AgentProfile] = []
        self._package_command_dirs: list[Path] = []
        self._package_subagent_dirs: list[Path] = []

    # -------------------------------------------------------------------------
    # Profile management
    # -------------------------------------------------------------------------

    def set_active_profile(self, profile: AgentProfile | None) -> None:
        """Set or clear the active profile. Call reload() afterwards to apply.

        Args:
            profile: The AgentProfile to activate, or None to deactivate.
        """
        self._active_profile = profile

    def _project_resource_dir(self, name: str) -> Path | None:
        """Project-local resource dir <cwd>/.operator/<name>, if it exists.

        Lets a repo ship its own extensions/skills/tools/commands/subagents/
        workflows/hooks under a flat .operator/ folder that loads automatically
        when Operator runs in that repo."""
        d = self._cwd / CONFIG_DIR_NAME / name
        return d if d.is_dir() else None

    # -------------------------------------------------------------------------
    # Public interface
    # -------------------------------------------------------------------------

    def get_extensions(self) -> LoadExtensionsResult:
        """Return the loaded extensions (tools, commands, hooks) and errors."""
        return self._extensions_result

    def get_skills(self) -> tuple[list[Skill], list[ResourceDiagnostic]]:
        """Return discovered skills and any validation diagnostics."""
        return self._skills, self._skill_diagnostics

    def get_tools(self) -> list[Tool]:
        """Return all available tools (built-in, project, profile, and packages)."""
        return self._tools

    def get_commands(self) -> list[SlashCommandInfo]:
        """Return all registered slash commands (built-in and extension)."""
        return self._commands

    def get_hooks(self) -> list[HookRegistration]:
        """Return all registered event hooks (built-in and extension)."""
        return self._hooks

    def get_system_prompt(self) -> str | None:
        """Return the assembled system prompt for the agent."""
        return self._system_prompt

    def get_append_system_prompt(self) -> list[str]:
        """Return additional system prompt lines to append."""
        return self._append_system_prompt

    def get_soul_prompt(self) -> str | None:
        """Return the agent's persona/soul override from SOUL.md."""
        return self._soul_prompt

    def get_user_profile(self) -> str | None:
        """Return the user profile information from USER.md."""
        return self._user_profile

    def get_agent_memory(self) -> str | None:
        """Return the agent's memory context from MEMORY.md."""
        return self._agent_memory

    def get_tools_reference(self) -> str | None:
        """Return the tools reference documentation."""
        return self._tools_reference

    def extend_resources(self, paths: ResourceExtensionPaths) -> None:
        """Called after resources_discover to add extension-provided skill and workflow paths.

        Args:
            paths: ResourceExtensionPaths with skill_paths and workflow_paths from extensions.
        """
        new_skill_paths = [p for p in paths.skill_paths if p not in self._extension_skill_paths]
        if new_skill_paths:
            self._extension_skill_paths.extend(new_skill_paths)
            self._reload_skills()

        new_wf_paths = [p for p in paths.workflow_paths if p not in self._extension_workflow_paths]
        if new_wf_paths:
            self._extension_workflow_paths.extend(new_wf_paths)

    def get_workflow_dirs(self) -> list[Path]:
        """Return all workflow directories: builtins + extension-contributed + profile."""
        from operator_use.settings.paths import get_builtins_workflows_dir
        dirs: list[Path] = [get_builtins_workflows_dir()]
        dirs.extend(Path(p) for p in self._extension_workflow_paths)
        if self._active_profile is not None:
            dirs.append(self._active_profile.workflows_dir)
        if project_wf := self._project_resource_dir('workflows'):
            dirs.append(project_wf)
        return dirs

    def get_subagent_profiles(self) -> list[SubagentProfile]:
        """Return all discovered subagent profiles."""
        return self._subagent_profiles

    def get_agent_profiles(self) -> list[AgentProfile]:
        """Return all discovered agent profiles."""
        return self._agent_profiles

    def get_diagnostics(self, runtime: ExtensionRuntime | None = None) -> list[ResourceDiagnostic]:
        """Return all resource loading diagnostics (warnings and errors).

        Args:
            runtime: Optional ExtensionRuntime to include extension diagnostics.

        Returns:
            List of ResourceDiagnostic messages.
        """
        from operator_use.skill.types import LoadSkillsResult
        skills_result = LoadSkillsResult(skills=self._skills, diagnostics=self._skill_diagnostics)
        return run_diagnostics(self._extensions_result, skills_result=skills_result, runtime=runtime)

    async def reload(self) -> None:
        """Discover and reload all resources (extensions, skills, tools, commands, hooks, profiles).

        Called on initialization and whenever settings or profile changes.
        """
        await self._reload_extensions()
        self._reload_skills()
        self._reload_tools()
        self._reload_commands()
        self._reload_hooks()
        self._reload_system_prompt()
        self._reload_identity_files()
        self._reload_subagent_profiles()
        self._reload_agent_profiles()

    # -------------------------------------------------------------------------
    # Internal reload helpers
    # -------------------------------------------------------------------------

    async def _reload_extensions(self) -> None:
        """Discover and load all extension files from builtin, profile, and package directories."""
        if self._no_extensions:
            self._extensions_result = LoadExtensionsResult()
            return

        dirs: list[Path] = [get_builtins_extensions_dir()]

        if self._active_profile is not None:
            profile_ext = self._active_profile.extensions_dir
            if profile_ext.is_dir():
                dirs.append(profile_ext)

        if project_ext := self._project_resource_dir('extensions'):
            dirs.append(project_ext)

        dirs.extend(self._additional_extension_dirs)

        if self._package_sources and self._packages_dir:
            loaded = load_packages_from_settings(self._package_sources, self._packages_dir, self._cwd)
            self._package_skill_paths = loaded.skill_paths
            self._package_command_dirs = loaded.command_dirs
            self._package_subagent_dirs = loaded.subagent_dirs
            dirs.extend(loaded.extension_dirs)
        else:
            self._package_skill_paths: list[str] = []
            self._package_command_dirs = []
            self._package_subagent_dirs = []

        self._extensions_result = await discover_and_load_extensions(
            dirs, self._bus,
            disabled_stems=self._disabled_extension_stems or None,
            entry_configs=self._extension_configs or None,
        )

    def _reload_skills(self) -> None:
        """Load all skill files from builtin, profile, extension, and package directories."""
        if self._no_skills:
            self._skills = []
            self._skill_diagnostics = []
            return

        skill_paths = [str(get_builtins_skills_dir())]

        if self._active_profile is not None:
            profile_skills = self._active_profile.skills_dir
            if profile_skills.is_dir():
                skill_paths.append(str(profile_skills))

        if project_skills := self._project_resource_dir('skills'):
            skill_paths.append(str(project_skills))

        skill_paths.extend(self._extension_skill_paths)
        skill_paths.extend(getattr(self, '_package_skill_paths', []))

        result = load_skills(
            LoadSkillsOptions(
                cwd=self._cwd,
                skill_paths=skill_paths,
                include_defaults=True,
            ),
        )
        self._skills = result.skills
        self._skill_diagnostics = result.diagnostics

    def _reload_tools(self) -> None:
        """Load all tool files from builtin, profile, and project directories."""
        dirs = [get_builtins_tools_dir()]

        if self._active_profile is not None:
            profile_tools = self._active_profile.tools_dir
            if profile_tools.is_dir():
                dirs.append(profile_tools)

        if project_tools := self._project_resource_dir('tools'):
            dirs.append(project_tools)

        self._tools = load_tools(dirs).tools

    def _reload_commands(self) -> None:
        """Load all command files from builtin, profile, package, and project directories."""
        dirs = [get_builtins_commands_dir()]

        if self._active_profile is not None:
            profile_cmds = self._active_profile.commands_dir
            if profile_cmds.is_dir():
                dirs.append(profile_cmds)

        if project_cmds := self._project_resource_dir('commands'):
            dirs.append(project_cmds)

        dirs.extend(self._package_command_dirs)
        self._commands = load_commands(dirs).commands

    def _reload_hooks(self) -> None:
        """Load all hook registrations from builtin, profile, and project directories."""
        dirs = [get_builtins_hooks_dir()]

        if self._active_profile is not None:
            profile_hooks = self._active_profile.hooks_dir
            if profile_hooks.is_dir():
                dirs.append(profile_hooks)

        if project_hooks := self._project_resource_dir('hooks'):
            dirs.append(project_hooks)

        self._hooks = load_hooks(dirs).hooks

    def _reload_subagent_profiles(self) -> None:
        """Load all subagent profile files from builtin, profile, package, and project directories."""
        dirs = [get_builtins_subagents_dir()]

        if self._active_profile is not None:
            profile_sub = self._active_profile.subagents_dir
            if profile_sub.is_dir():
                dirs.append(profile_sub)

        if project_sub := self._project_resource_dir('subagents'):
            dirs.append(project_sub)

        dirs.extend(self._package_subagent_dirs)
        self._subagent_profiles = load_profiles(dirs).profiles

    def _reload_agent_profiles(self) -> None:
        """Load all agent profiles from the global profiles directory."""
        profiles_root = get_profiles_dir()
        dirs = [profiles_root] if profiles_root.is_dir() else []
        self._agent_profiles = load_agent_profiles(dirs).profiles

    def _reload_system_prompt(self) -> None:
        """Assemble system prompt from override, knowledge, and workflow sections."""
        if self._system_prompt_override is not None:
            self._system_prompt = self._system_prompt_override
        else:
            self._system_prompt = None

        if self._append_system_prompt_override:
            self._append_system_prompt = list(self._append_system_prompt_override)
        else:
            self._append_system_prompt = []

        # Knowledge section from profile
        if self._active_profile is not None:
            from operator_use.knowledge.service import Knowledge
            knowledge = Knowledge(self._active_profile.knowledge_dir)
            if section := knowledge.build_knowledge_for_prompt():
                self._append_system_prompt.append(section)

        # Workflows section from profile
        if self._active_profile is not None:
            from operator_use.workflow.loader import WorkflowLoader
            loader = WorkflowLoader(self._active_profile.workflows_dir)
            workflows = loader.list_with_meta()
            if workflows:
                lines = ['## Workflows\n', '<available_workflows>']
                for _, meta in workflows:
                    line = f'  <workflow name="{meta.name}" description="{meta.description}"'
                    if meta.when_to_use:
                        line += f' when_to_use="{meta.when_to_use}"'
                    lines.append(line + ' />')
                lines.append('</available_workflows>')
                lines.append('\nUse the `workflow` tool with action="run" to execute a workflow.')
                self._append_system_prompt.append('\n'.join(lines))

        if self._active_profile is not None:
            self._active_profile.temp_dir.mkdir(parents=True, exist_ok=True)

    def _reload_identity_files(self) -> None:
        """Load identity files (soul, user profile, memory, tools reference) from the active profile."""
        if self._active_profile is not None:
            self._soul_prompt = _read_optional_file(self._active_profile.soul_path)
            self._user_profile = _read_optional_file(self._active_profile.user_path)
            self._agent_memory = _read_optional_file(self._active_profile.memory_path)
            self._tools_reference = _read_optional_file(self._active_profile.tools_path)
        else:
            self._soul_prompt = None
            self._user_profile = None
            self._agent_memory = None
            self._tools_reference = None

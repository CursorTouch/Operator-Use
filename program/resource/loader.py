"""
Resource loader — discovers, loads, and manages all agent resources:
extensions, skills, prompt templates, themes, AGENTS/CLAUDE context files,
and system prompt files.

Mirrors resource-loader.ts from the TS source.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from program.extensions.loader import (
    create_extension_runtime,
    load_extension_from_factory,
    load_extensions,
)
from program.extensions.types import (
    Extension,
    ExtensionFactory,
    ExtensionRuntime,
    LoadExtensionsResult,
)
from program.settings.manager import SettingsManager
from program.settings.paths import CONFIG_DIR_NAME, CONFIG_DIR_PATH
from program.skill import LoadSkillsResult
from program.skill.loader import load_skills
from program.skill.types import Skill

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

@dataclass
class ResourceCollision:
    resource_type: str          # "prompt" | "theme" | "skill" | "extension"
    name: str
    winner_path: str
    loser_path: str


@dataclass
class ResourceDiagnostic:
    type: str                   # "error" | "warning" | "collision"
    message: str
    path: str
    collision: Optional[ResourceCollision] = None


@dataclass
class PathMetadata:
    source: str                 # "local" | "cli" | "package" | "auto" | "settings"
    scope: str                  # "user" | "project" | "temporary"
    origin: str                 # "top-level" | "package"


@dataclass
class SourceInfo:
    path: str
    source: str
    scope: str
    origin: str
    base_dir: Optional[str] = None


@dataclass
class PromptTemplate:
    name: str
    content: str
    file_path: str
    source_info: Optional[SourceInfo] = None


# ---------------------------------------------------------------------------
# Source info helpers
# ---------------------------------------------------------------------------

def create_source_info(path: str, metadata: PathMetadata) -> SourceInfo:
    return SourceInfo(
        path=path,
        source=metadata.source,
        scope=metadata.scope,
        origin=metadata.origin,
    )


# ---------------------------------------------------------------------------
# Path utilities
# ---------------------------------------------------------------------------

def _canonicalize_path(p: str) -> str:
    """Resolve symlinks and normalise case (best-effort cross-platform)."""
    try:
        return os.path.realpath(p)
    except OSError:
        return os.path.normpath(p)


def _is_local_path(p: str) -> bool:
    """True for paths that refer to the local filesystem (not package names)."""
    stripped = p.strip()
    return stripped.startswith(("/", ".", "~")) or (
        len(stripped) > 1 and stripped[1] == ":"  # Windows drive letter
    )


def _resolve_resource_path(p: str, cwd: str) -> str:
    trimmed = p.strip()
    if trimmed == "~":
        expanded = str(Path.home())
    elif trimmed.startswith("~/"):
        expanded = str(Path.home() / trimmed[2:])
    elif trimmed.startswith("~"):
        expanded = str(Path.home() / trimmed[1:])
    else:
        expanded = trimmed
    return os.path.normpath(os.path.join(cwd, expanded))


def _is_under_path(target: str, root: str) -> bool:
    norm_root = os.path.normpath(root)
    if target == norm_root:
        return True
    return target.startswith(norm_root + os.sep)


# ---------------------------------------------------------------------------
# Prompt templates loader
# ---------------------------------------------------------------------------

def load_prompt_templates(
    cwd: str,
    agent_dir: str,
    prompt_paths: list[str],
    include_defaults: bool = False,
) -> dict:
    prompts: list[PromptTemplate] = []
    diagnostics: list[ResourceDiagnostic] = []

    paths_to_load: list[str] = []
    if include_defaults:
        paths_to_load += [
            os.path.join(agent_dir, "prompts"),
            os.path.join(cwd, CONFIG_DIR_NAME, "prompts"),
        ]
    paths_to_load += prompt_paths

    for p in paths_to_load:
        if not os.path.exists(p):
            continue
        try:
            if os.path.isfile(p) and p.endswith(".md"):
                prompt = _load_prompt_from_file(p)
                if prompt:
                    prompts.append(prompt)
            elif os.path.isdir(p):
                _load_prompts_from_dir(p, prompts, diagnostics)
        except OSError as exc:
            diagnostics.append(ResourceDiagnostic(type="warning", message=str(exc), path=p))

    return {"prompts": prompts, "diagnostics": diagnostics}


def _load_prompt_from_file(file_path: str) -> Optional[PromptTemplate]:
    try:
        with open(file_path, encoding="utf-8") as fh:
            content = fh.read()
        name = os.path.splitext(os.path.basename(file_path))[0]
        return PromptTemplate(name=name, content=content, file_path=file_path)
    except OSError:
        return None


def _load_prompts_from_dir(directory: str, prompts: list, diagnostics: list) -> None:
    try:
        for entry in sorted(os.scandir(directory), key=lambda e: e.name):
            if entry.is_file() and entry.name.endswith(".md"):
                prompt = _load_prompt_from_file(entry.path)
                if prompt:
                    prompts.append(prompt)
    except OSError as exc:
        diagnostics.append(ResourceDiagnostic(type="warning", message=str(exc), path=directory))


# ---------------------------------------------------------------------------
# Context files  (AGENTS.md / CLAUDE.md)
# ---------------------------------------------------------------------------

_CONTEXT_FILE_CANDIDATES = ("AGENTS.md", "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD")


def _load_context_file_from_dir(directory: str) -> Optional[dict]:
    for filename in _CONTEXT_FILE_CANDIDATES:
        file_path = os.path.join(directory, filename)
        if os.path.isfile(file_path):
            try:
                with open(file_path, encoding="utf-8") as fh:
                    return {"path": file_path, "content": fh.read()}
            except OSError as exc:
                logger.warning("Could not read %s: %s", file_path, exc)
    return None


def load_project_context_files(cwd: str, agent_dir: str) -> list[dict]:
    """
    Walk from cwd to root, collecting AGENTS.md/CLAUDE.md files.
    Global agent-dir file prepended; ancestor files in traversal order (root → cwd).
    """
    context_files: list[dict] = []
    seen: set[str] = set()

    global_ctx = _load_context_file_from_dir(agent_dir)
    if global_ctx:
        context_files.append(global_ctx)
        seen.add(global_ctx["path"])

    ancestor_files: list[dict] = []
    current = os.path.realpath(cwd)
    root = os.path.realpath("/")

    while True:
        ctx = _load_context_file_from_dir(current)
        if ctx and ctx["path"] not in seen:
            ancestor_files.insert(0, ctx)
            seen.add(ctx["path"])

        if current == root:
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    context_files.extend(ancestor_files)
    return context_files


# ---------------------------------------------------------------------------
# Stub package manager
# (Full package manager not yet implemented; reads directly from settings.)
# ---------------------------------------------------------------------------

class _PackageManager:
    """
    Minimal package manager that resolves paths from settings only.
    Handles no npm/pip packages — those can be wired in later.
    """

    def __init__(self, cwd: str, agent_dir: str, settings_manager: SettingsManager) -> None:
        self._cwd = cwd
        self._agent_dir = agent_dir
        self._sm = settings_manager

    def _meta(self, source: str = "settings", scope: str = "user") -> PathMetadata:
        return PathMetadata(source=source, scope=scope, origin="top-level")

    def _wrap(self, paths: list[str], source: str = "settings", scope: str = "user") -> list[dict]:
        return [{"path": p, "enabled": True, "metadata": self._meta(source, scope)} for p in paths]

    async def resolve(self) -> dict:
        settings = self._sm.settings

        # Discover paths from standard dirs
        local_ext_dir = os.path.join(self._cwd, CONFIG_DIR_NAME, "extensions")
        global_ext_dir = os.path.join(self._agent_dir, "extensions")
        local_skill_dir = os.path.join(self._cwd, CONFIG_DIR_NAME, "skills")
        global_skill_dir = os.path.join(self._agent_dir, "skills")
        local_prompt_dir = os.path.join(self._cwd, CONFIG_DIR_NAME, "prompts")
        global_prompt_dir = os.path.join(self._agent_dir, "prompts")

        def _dir_entries(directory: str, scope: str) -> list[dict]:
            if not os.path.isdir(directory):
                return []
            return [{"path": directory, "enabled": True, "metadata": self._meta("local", scope)}]

        extensions = (
            _dir_entries(global_ext_dir, "user")
            + _dir_entries(local_ext_dir, "project")
            + self._wrap(settings.extensions or [], "settings", "user")
        )
        skills = (
            _dir_entries(global_skill_dir, "user")
            + _dir_entries(local_skill_dir, "project")
            + self._wrap(settings.skills or [], "settings", "user")
        )
        prompts = (
            _dir_entries(global_prompt_dir, "user")
            + _dir_entries(local_prompt_dir, "project")
            + self._wrap(settings.prompts or [], "settings", "user")
        )

        return {
            "extensions": extensions,
            "skills": skills,
            "prompts": prompts,
        }

    async def resolve_extension_sources(
        self, paths: list[str], options: Optional[dict] = None
    ) -> dict:
        meta = PathMetadata(source="cli", scope="temporary", origin="top-level")
        return {
            "extensions": [{"path": p, "enabled": True, "metadata": meta} for p in paths],
            "skills": [],
            "prompts": [],
        }


# ---------------------------------------------------------------------------
# ResourceLoader interface and implementation
# ---------------------------------------------------------------------------

@dataclass
class ResourceExtensionPaths:
    skill_paths: list[dict] = field(default_factory=list)   # [{path, metadata}]
    prompt_paths: list[dict] = field(default_factory=list)


@dataclass
class DefaultResourceLoaderOptions:
    cwd: str
    agent_dir: str
    settings_manager: Optional[SettingsManager] = None
    additional_extension_paths: list[str] = field(default_factory=list)
    additional_skill_paths: list[str] = field(default_factory=list)
    additional_prompt_template_paths: list[str] = field(default_factory=list)
    extension_factories: list[ExtensionFactory] = field(default_factory=list)
    no_extensions: bool = False
    no_skills: bool = False
    no_prompt_templates: bool = False
    no_context_files: bool = False
    system_prompt: Optional[str] = None
    append_system_prompt: Optional[list[str]] = None
    extensions_override: Optional[Callable] = None
    skills_override: Optional[Callable] = None
    prompts_override: Optional[Callable] = None
    agents_files_override: Optional[Callable] = None
    system_prompt_override: Optional[Callable] = None
    append_system_prompt_override: Optional[Callable] = None


def _resolve_prompt_input(value: Optional[str], description: str) -> Optional[str]:
    """If *value* is a path to an existing file, read and return its contents."""
    if not value:
        return None
    if os.path.isfile(value):
        try:
            with open(value, encoding="utf-8") as fh:
                return fh.read()
        except OSError as exc:
            logger.warning("Could not read %s file %s: %s", description, value, exc)
            return value
    return value


class DefaultResourceLoader:
    def __init__(self, options: DefaultResourceLoaderOptions) -> None:
        self._cwd = options.cwd
        self._agent_dir = options.agent_dir
        self._settings_manager = options.settings_manager or SettingsManager.create(
            Path(options.cwd), Path(options.agent_dir)
        )
        self._package_manager = _PackageManager(
            cwd=self._cwd,
            agent_dir=self._agent_dir,
            settings_manager=self._settings_manager,
        )
        self._additional_extension_paths = options.additional_extension_paths
        self._additional_skill_paths = options.additional_skill_paths
        self._additional_prompt_template_paths = options.additional_prompt_template_paths
        self._extension_factories = options.extension_factories
        self._no_extensions = options.no_extensions
        self._no_skills = options.no_skills
        self._no_prompt_templates = options.no_prompt_templates
        self._no_context_files = options.no_context_files
        self._system_prompt_source = options.system_prompt
        self._append_system_prompt_source = options.append_system_prompt
        self._extensions_override = options.extensions_override
        self._skills_override = options.skills_override
        self._prompts_override = options.prompts_override
        self._agents_files_override = options.agents_files_override
        self._system_prompt_override = options.system_prompt_override
        self._append_system_prompt_override = options.append_system_prompt_override

        # Loaded state
        self._extensions_result: LoadExtensionsResult = LoadExtensionsResult(
            extensions=[], errors=[], runtime=create_extension_runtime()
        )
        self._skills: list[Skill] = []
        self._skill_diagnostics: list[dict] = []
        self._prompts: list[PromptTemplate] = []
        self._prompt_diagnostics: list[ResourceDiagnostic] = []
        self._agents_files: list[dict] = []
        self._system_prompt: Optional[str] = None
        self._append_system_prompt: list[str] = []

        self._last_skill_paths: list[str] = []
        self._last_prompt_paths: list[str] = []
        self._extension_skill_source_infos: dict[str, SourceInfo] = {}
        self._extension_prompt_source_infos: dict[str, SourceInfo] = {}

    # -------------------------------------------------------------------------
    # Accessors
    # -------------------------------------------------------------------------

    def get_extensions(self) -> LoadExtensionsResult:
        return self._extensions_result

    def get_skills(self) -> dict:
        return {"skills": self._skills, "diagnostics": self._skill_diagnostics}

    def get_prompts(self) -> dict:
        return {"prompts": self._prompts, "diagnostics": self._prompt_diagnostics}

    def get_agents_files(self) -> dict:
        return {"agents_files": self._agents_files}

    def get_system_prompt(self) -> Optional[str]:
        return self._system_prompt

    def get_append_system_prompt(self) -> list[str]:
        return self._append_system_prompt

    # -------------------------------------------------------------------------
    # extendResources — called by extension runner after resources_discover
    # -------------------------------------------------------------------------

    def extend_resources(self, paths: ResourceExtensionPaths) -> None:
        skill_entries = self._normalize_extension_paths(paths.skill_paths)
        prompt_entries = self._normalize_extension_paths(paths.prompt_paths)

        for entry in skill_entries:
            meta = PathMetadata(**entry["metadata"]) if isinstance(entry["metadata"], dict) else entry["metadata"]
            self._extension_skill_source_infos[entry["path"]] = create_source_info(entry["path"], meta)
        for entry in prompt_entries:
            meta = PathMetadata(**entry["metadata"]) if isinstance(entry["metadata"], dict) else entry["metadata"]
            self._extension_prompt_source_infos[entry["path"]] = create_source_info(entry["path"], meta)

        if skill_entries:
            self._last_skill_paths = self._merge_paths(
                self._last_skill_paths, [e["path"] for e in skill_entries]
            )
            self._update_skills_from_paths(self._last_skill_paths)

        if prompt_entries:
            self._last_prompt_paths = self._merge_paths(
                self._last_prompt_paths, [e["path"] for e in prompt_entries]
            )
            self._update_prompts_from_paths(self._last_prompt_paths)

    # -------------------------------------------------------------------------
    # reload
    # -------------------------------------------------------------------------

    async def reload(self) -> None:
        await self._settings_manager.reload()
        resolved = await self._package_manager.resolve()
        cli_sources = await self._package_manager.resolve_extension_sources(
            self._additional_extension_paths, {"temporary": True}
        )

        metadata_by_path: dict[str, PathMetadata] = {}

        self._extension_skill_source_infos = {}
        self._extension_prompt_source_infos = {}

        def _get_enabled(resources: list[dict]) -> list[str]:
            for r in resources:
                key = os.path.normpath(r["path"])
                meta = r["metadata"]
                if not isinstance(meta, PathMetadata):
                    meta = PathMetadata(**meta) if isinstance(meta, dict) else meta
                if key not in metadata_by_path:
                    metadata_by_path[key] = meta
            return [r["path"] for r in resources if r.get("enabled", True)]

        def _get_enabled_resources(resources: list[dict]) -> list[dict]:
            for r in resources:
                key = os.path.normpath(r["path"])
                meta = r["metadata"]
                if not isinstance(meta, PathMetadata):
                    meta = PathMetadata(**meta) if isinstance(meta, dict) else meta
                if key not in metadata_by_path:
                    metadata_by_path[key] = meta
            return [r for r in resources if r.get("enabled", True)]

        enabled_ext_paths = _get_enabled(resolved["extensions"])
        enabled_skill_resources = _get_enabled_resources(resolved["skills"])
        enabled_prompt_paths = _get_enabled(resolved["prompts"])

        # Map skill paths: dirs with SKILL.md get resolved to that file
        def _map_skill_path(resource: dict) -> str:
            p = resource["path"]
            meta = resource.get("metadata", {})
            source = meta.get("source") if isinstance(meta, dict) else getattr(meta, "source", "")
            origin = meta.get("origin") if isinstance(meta, dict) else getattr(meta, "origin", "")
            if source not in ("auto",) and origin != "package":
                return p
            if os.path.isdir(p):
                skill_file = os.path.join(p, "SKILL.md")
                if os.path.isfile(skill_file):
                    key = os.path.normpath(skill_file)
                    if key not in metadata_by_path and os.path.normpath(p) in metadata_by_path:
                        metadata_by_path[key] = metadata_by_path[os.path.normpath(p)]
                    return skill_file
            return p

        enabled_skills = [_map_skill_path(r) for r in enabled_skill_resources]

        # Metadata for CLI-sourced paths
        cli_meta = PathMetadata(source="cli", scope="temporary", origin="top-level")
        for r in cli_sources.get("extensions", []):
            key = os.path.normpath(r["path"])
            if key not in metadata_by_path:
                metadata_by_path[key] = cli_meta
        for r in cli_sources.get("skills", []):
            key = os.path.normpath(r["path"])
            if key not in metadata_by_path:
                metadata_by_path[key] = cli_meta

        cli_ext_paths = _get_enabled(cli_sources.get("extensions", []))
        cli_skill_paths = _get_enabled(cli_sources.get("skills", []))
        cli_prompt_paths = _get_enabled(cli_sources.get("prompts", []))

        # Extensions
        ext_paths = (
            cli_ext_paths
            if self._no_extensions
            else self._merge_paths(cli_ext_paths, enabled_ext_paths)
        )
        ext_result = await load_extensions(ext_paths, self._cwd)
        inline = await self._load_extension_factories(ext_result.runtime)
        ext_result.extensions.extend(inline["extensions"])
        ext_result.errors.extend(inline["errors"])

        for conflict in self._detect_extension_conflicts(ext_result.extensions):
            ext_result.errors.append(conflict)

        for p in self._additional_extension_paths:
            if _is_local_path(p) and not os.path.exists(p):
                ext_result.errors.append({"path": p, "error": f"Extension path does not exist: {p}"})

        self._extensions_result = (
            self._extensions_override(ext_result) if self._extensions_override else ext_result
        )
        self._apply_extension_source_info(self._extensions_result.extensions, metadata_by_path)

        # Skills
        skill_paths = (
            self._merge_paths(cli_skill_paths, self._additional_skill_paths)
            if self._no_skills
            else self._merge_paths(cli_skill_paths + enabled_skills, self._additional_skill_paths)
        )
        self._last_skill_paths = skill_paths
        self._update_skills_from_paths(skill_paths, metadata_by_path)
        for p in self._additional_skill_paths:
            if _is_local_path(p) and not os.path.exists(p):
                if not any(d.get("path") == p for d in self._skill_diagnostics):
                    self._skill_diagnostics.append(
                        {"type": "error", "message": "Skill path does not exist", "path": p}
                    )

        # Prompts
        prompt_paths = (
            self._merge_paths(cli_prompt_paths, self._additional_prompt_template_paths)
            if self._no_prompt_templates
            else self._merge_paths(cli_prompt_paths + enabled_prompt_paths, self._additional_prompt_template_paths)
        )
        self._last_prompt_paths = prompt_paths
        self._update_prompts_from_paths(prompt_paths, metadata_by_path)
        for p in self._additional_prompt_template_paths:
            if _is_local_path(p) and not os.path.exists(p):
                if not any(d.path == p for d in self._prompt_diagnostics):
                    self._prompt_diagnostics.append(
                        ResourceDiagnostic(type="error", message="Prompt template path does not exist", path=p)
                    )

        # Agents / context files
        agents_base = {
            "agents_files": (
                [] if self._no_context_files
                else load_project_context_files(self._cwd, self._agent_dir)
            )
        }
        resolved_agents = self._agents_files_override(agents_base) if self._agents_files_override else agents_base
        self._agents_files = resolved_agents["agents_files"]

        # System prompt
        base_system_prompt = _resolve_prompt_input(
            self._system_prompt_source or self._discover_system_prompt_file(),
            "system prompt",
        )
        self._system_prompt = (
            self._system_prompt_override(base_system_prompt)
            if self._system_prompt_override
            else base_system_prompt
        )

        # Append system prompt
        append_sources = self._append_system_prompt_source or (
            [f] if (f := self._discover_append_system_prompt_file()) else []
        )
        base_append = [
            s for src in append_sources
            if (s := _resolve_prompt_input(src, "append system prompt")) is not None
        ]
        self._append_system_prompt = (
            self._append_system_prompt_override(base_append)
            if self._append_system_prompt_override
            else base_append
        )

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    def _normalize_extension_paths(self, entries: list[dict]) -> list[dict]:
        return [
            {"path": _resolve_resource_path(e["path"], self._cwd), "metadata": e["metadata"]}
            for e in entries
        ]

    def _merge_paths(self, primary: list[str], additional: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for p in primary + additional:
            resolved = _resolve_resource_path(p, self._cwd)
            key = _canonicalize_path(resolved)
            if key not in seen:
                seen.add(key)
                merged.append(resolved)
        return merged

    def _find_source_info_for_path(
        self,
        resource_path: str,
        extra: Optional[dict[str, SourceInfo]] = None,
        metadata_by_path: Optional[dict[str, PathMetadata]] = None,
    ) -> Optional[SourceInfo]:
        if not resource_path:
            return None
        if resource_path.startswith("<"):
            return self._get_default_source_info(resource_path)

        norm = os.path.normpath(os.path.realpath(resource_path))

        if extra:
            for src_path, src_info in extra.items():
                norm_src = os.path.normpath(os.path.realpath(src_path))
                if norm == norm_src or norm.startswith(norm_src + os.sep):
                    return SourceInfo(**{**src_info.__dict__, "path": resource_path})

        if metadata_by_path:
            meta = metadata_by_path.get(norm) or metadata_by_path.get(resource_path)
            if meta:
                return create_source_info(resource_path, meta)
            for src_path, meta in metadata_by_path.items():
                norm_src = os.path.normpath(os.path.realpath(src_path))
                if norm == norm_src or norm.startswith(norm_src + os.sep):
                    return create_source_info(resource_path, meta)

        return None

    def _get_default_source_info(self, file_path: str) -> SourceInfo:
        if file_path.startswith("<") and file_path.endswith(">"):
            return SourceInfo(
                path=file_path,
                source=file_path[1:-1].split(":")[0] or "temporary",
                scope="temporary",
                origin="top-level",
            )
        norm = os.path.normpath(os.path.realpath(file_path))
        agent_roots = [
            os.path.join(self._agent_dir, sub)
            for sub in ("skills", "prompts", "themes", "extensions")
        ]
        project_roots = [
            os.path.join(self._cwd, CONFIG_DIR_NAME, sub)
            for sub in ("skills", "prompts", "themes", "extensions")
        ]
        for root in agent_roots:
            if _is_under_path(norm, root):
                return SourceInfo(path=file_path, source="local", scope="user", origin="top-level", base_dir=root)
        for root in project_roots:
            if _is_under_path(norm, root):
                return SourceInfo(path=file_path, source="local", scope="project", origin="top-level", base_dir=root)
        base = norm if os.path.isdir(norm) else os.path.dirname(norm)
        return SourceInfo(path=file_path, source="local", scope="temporary", origin="top-level", base_dir=base)

    def _apply_extension_source_info(
        self, extensions: list[Extension], metadata_by_path: dict[str, PathMetadata]
    ) -> None:
        for ext in extensions:
            src = (
                self._find_source_info_for_path(ext.path, None, metadata_by_path)
                or self._get_default_source_info(ext.path)
            )
            ext.source_info = src
            for cmd in ext.commands.values():
                cmd.source_info = src
            for tool in ext.tools.values():
                tool.source_info = src

    def _update_skills_from_paths(
        self, paths: list[str], metadata_by_path: Optional[dict] = None
    ) -> None:
        if self._no_skills and not paths:
            result = LoadSkillsResult()
        else:
            result = load_skills(self._cwd, self._agent_dir, paths, include_defaults=False)
        if self._skills_override:
            raw = self._skills_override({"skills": result.skills, "diagnostics": result.diagnostics})
            result = LoadSkillsResult(skills=raw.get("skills", []), diagnostics=raw.get("diagnostics", []))
        self._skills = [
            Skill(
                **{
                    **s.__dict__,
                    "source_info": (
                        self._find_source_info_for_path(
                            s.file_path, self._extension_skill_source_infos, metadata_by_path
                        )
                        or s.source_info
                        or self._get_default_source_info(s.file_path)
                    ),
                }
            )
            for s in result.skills
        ]
        self._skill_diagnostics = result.diagnostics

    def _update_prompts_from_paths(
        self, paths: list[str], metadata_by_path: Optional[dict] = None
    ) -> None:
        if self._no_prompt_templates and not paths:
            result = {"prompts": [], "diagnostics": []}
        else:
            raw = load_prompt_templates(self._cwd, self._agent_dir, paths, include_defaults=False)
            result = self._dedupe_prompts(raw)
        result = self._prompts_override(result) if self._prompts_override else result
        self._prompts = [
            PromptTemplate(
                **{
                    **p.__dict__,
                    "source_info": (
                        self._find_source_info_for_path(
                            p.file_path, self._extension_prompt_source_infos, metadata_by_path
                        )
                        or p.source_info
                        or self._get_default_source_info(p.file_path)
                    ),
                }
            )
            for p in result["prompts"]
        ]
        self._prompt_diagnostics = result["diagnostics"]

    def _dedupe_prompts(self, raw: dict) -> dict:
        seen: dict[str, PromptTemplate] = {}
        diagnostics: list[ResourceDiagnostic] = list(raw.get("diagnostics", []))
        for prompt in raw.get("prompts", []):
            if prompt.name in seen:
                diagnostics.append(ResourceDiagnostic(
                    type="collision",
                    message=f'name "/{prompt.name}" collision',
                    path=prompt.file_path,
                    collision=ResourceCollision(
                        resource_type="prompt",
                        name=prompt.name,
                        winner_path=seen[prompt.name].file_path,
                        loser_path=prompt.file_path,
                    ),
                ))
            else:
                seen[prompt.name] = prompt
        return {"prompts": list(seen.values()), "diagnostics": diagnostics}

    def _discover_system_prompt_file(self) -> Optional[str]:
        for base in (self._cwd, self._agent_dir):
            candidate = (
                os.path.join(base, CONFIG_DIR_NAME, "SYSTEM.md")
                if base == self._cwd
                else os.path.join(base, "SYSTEM.md")
            )
            if os.path.isfile(candidate):
                return candidate
        return None

    def _discover_append_system_prompt_file(self) -> Optional[str]:
        for base in (self._cwd, self._agent_dir):
            candidate = (
                os.path.join(base, CONFIG_DIR_NAME, "APPEND_SYSTEM.md")
                if base == self._cwd
                else os.path.join(base, "APPEND_SYSTEM.md")
            )
            if os.path.isfile(candidate):
                return candidate
        return None

    async def _load_extension_factories(self, runtime: ExtensionRuntime) -> dict:
        extensions: list[Extension] = []
        errors: list[dict] = []
        for i, factory in enumerate(self._extension_factories):
            ext_path = f"<inline:{i + 1}>"
            try:
                ext = await load_extension_from_factory(factory, self._cwd, runtime, ext_path)
                extensions.append(ext)
            except Exception as exc:
                errors.append({"path": ext_path, "error": str(exc)})
        return {"extensions": extensions, "errors": errors}

    def _detect_extension_conflicts(self, extensions: list[Extension]) -> list[dict]:
        conflicts: list[dict] = []
        tool_owners: dict[str, str] = {}
        flag_owners: dict[str, str] = {}
        for ext in extensions:
            for tool_name in ext.tools:
                existing = tool_owners.get(tool_name)
                if existing and existing != ext.path:
                    conflicts.append({"path": ext.path, "error": f'Tool "{tool_name}" conflicts with {existing}'})
                else:
                    tool_owners[tool_name] = ext.path
            for flag_name in ext.flags:
                existing = flag_owners.get(flag_name)
                if existing and existing != ext.path:
                    conflicts.append({"path": ext.path, "error": f'Flag "--{flag_name}" conflicts with {existing}'})
                else:
                    flag_owners[flag_name] = ext.path
        return conflicts

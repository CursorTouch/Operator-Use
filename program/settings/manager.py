from __future__ import annotations
import copy
import dataclasses as dc
import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Set, Dict, List, Any, Callable

from program.settings.storage import SettingsStorage, FileSettingsStorage, InMemorySettingsStorage, LockResult
from program.settings.types import (
    Settings, SCOPE, SettingsError,
    CompactionSettings, BranchSummarySettings,
    RetrySettings, ProviderRetrySettings, ThinkingBudgetsSettings,
)
from program.engine.types import SteeringMode, FollowupMode
from program.inference.types import Transport, ThinkingLevel

_NESTED_FIELD_TYPES: dict[str, type] = {
    'compaction': CompactionSettings,
    'branch_summary': BranchSummarySettings,
    'thinking_budgets': ThinkingBudgetsSettings,
}


class SettingsManager:
    def __init__(
        self,
        storage: SettingsStorage,
        initial_global: Settings,
        initial_project: Settings,
        global_load_error: Optional[Exception] = None,
        project_load_error: Optional[Exception] = None,
        initial_errors: List[SettingsError] = None,
    ):
        """Initialise with pre-loaded global and project settings and any load errors."""
        self.storage = storage
        self.global_settings = initial_global
        self.project_settings = initial_project
        self.settings = self._deep_merge_settings(initial_global, initial_project)
        self.modified_fields: Set[str] = set()
        self.modified_nested_fields: Dict[str, Set[str]] = {}
        self.modified_project_fields: Set[str] = set()
        self.modified_project_nested_fields: Dict[str, Set[str]] = {}
        self.global_settings_load_error: Optional[Exception] = global_load_error
        self.project_settings_load_error: Optional[Exception] = project_load_error
        self.errors: List[SettingsError] = initial_errors.copy() if initial_errors else []
        self._write_queue = None

    @staticmethod
    def create(cwd: Path, agent_dir: Optional[Path] = None) -> SettingsManager:
        """Create a SettingsManager backed by files in cwd (and optional agent_dir for global settings)."""
        storage = FileSettingsStorage(cwd, agent_dir)
        return SettingsManager.from_storage(storage)

    @staticmethod
    def from_storage(storage: SettingsStorage) -> SettingsManager:
        """Create a SettingsManager from an arbitrary storage backend."""
        global_settings, global_error = SettingsManager._try_load_from_storage(storage, "global")
        project_settings, project_error = SettingsManager._try_load_from_storage(storage, "project")
        initial_errors = []
        if global_error:
            initial_errors.append(SettingsError(scope=SCOPE.GLOBAL, error=global_error))
        if project_error:
            initial_errors.append(SettingsError(scope=SCOPE.PROJECT, error=project_error))
        return SettingsManager(storage, global_settings, project_settings,
                global_error, project_error, initial_errors)

    @staticmethod
    def in_memory(settings: Dict = None) -> SettingsManager:
        """Create an in-memory SettingsManager with optional seed data (no file I/O, useful for testing)."""
        storage = InMemorySettingsStorage()
        settings_dict = settings or {}
        storage.with_lock(SCOPE.GLOBAL, lambda _: LockResult(result=None, next=json.dumps(settings_dict, indent=2)))
        return SettingsManager.from_storage(storage)

    @staticmethod
    def _settings_from_dict(data: dict) -> Settings:
        """Construct a Settings instance from a raw dict, rebuilding nested dataclasses from plain dicts."""
        valid_settings = {f.name for f in dc.fields(Settings)}
        kwargs: dict[str, Any] = {}
        for key, value in data.items():
            if key not in valid_settings:
                continue
            if key in _NESTED_FIELD_TYPES and isinstance(value, dict):
                nested_cls = _NESTED_FIELD_TYPES[key]
                valid_nested = {f.name for f in dc.fields(nested_cls)}
                kwargs[key] = nested_cls(**{k: v for k, v in value.items() if k in valid_nested})
            elif key == 'retry' and isinstance(value, dict):
                provider = value.get('provider')
                if isinstance(provider, dict):
                    valid_provider = {f.name for f in dc.fields(ProviderRetrySettings)}
                    provider = ProviderRetrySettings(**{k: v for k, v in provider.items() if k in valid_provider})
                valid_retry = {f.name for f in dc.fields(RetrySettings)}
                retry_kwargs = {k: v for k, v in value.items() if k in valid_retry and k != 'provider'}
                kwargs[key] = RetrySettings(**retry_kwargs, provider=provider)
            else:
                kwargs[key] = value
        return Settings(**kwargs)

    @staticmethod
    def _load_from_storage(storage: SettingsStorage, scope: SCOPE) -> Settings:
        """Read and parse settings for the given scope from storage."""
        def load_fn(current):
            if not current:
                return LockResult(result=Settings(), next=None)
            return LockResult(result=SettingsManager._settings_from_dict(json.loads(current)), next=None)
        return storage.with_lock(scope, load_fn).result

    @staticmethod
    def _try_load_from_storage(storage: SettingsStorage, scope: SCOPE) -> tuple[Settings, Optional[Exception]]:
        """Load settings for the given scope, returning an empty Settings and the error on failure."""
        try:
            return (SettingsManager._load_from_storage(storage, scope), None)
        except Exception as e:
            return (Settings(), e)

    def _deep_merge_settings(self, global_settings: Settings, project_settings: Settings) -> Settings:
        """Merge global and project settings; project wins, nested dataclasses merge field by field."""
        merged = copy.deepcopy(global_settings)
        for key, value in vars(project_settings).items():
            if value is None:
                continue
            existing = getattr(merged, key)
            if dc.is_dataclass(value) and existing is not None and dc.is_dataclass(existing):
                merged_nested = copy.deepcopy(existing)
                for f in dc.fields(value):
                    nested_val = getattr(value, f.name)
                    if nested_val is not None:
                        setattr(merged_nested, f.name, nested_val)
                setattr(merged, key, merged_nested)
            else:
                setattr(merged, key, value)
        return merged

    def _mark_modified(self, field: str, nested_field: Optional[str] = None):
        """Record a global settings field (and optional nested key) as modified."""
        self.modified_fields.add(field)
        if nested_field:
            self.modified_nested_fields.setdefault(field, set()).add(nested_field)

    def _mark_project_modified(self, field: str, nested_field: Optional[str] = None):
        """Record a project settings field (and optional nested key) as modified."""
        self.modified_project_fields.add(field)
        if nested_field:
            self.modified_project_nested_fields.setdefault(field, set()).add(nested_field)

    def _clone_modified_nested_fields(self, source: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
        """Snapshot the nested-field modification tracker so async writes see state at enqueue time."""
        return {key: set(value) for key, value in source.items()}

    def _record_error(self, scope: SCOPE, error: Exception):
        """Append a scoped error to the error queue for later retrieval via drain_errors()."""
        self.errors.append(SettingsError(scope=scope, error=error))

    def _clear_modified_scope(self, scope: SCOPE):
        """Reset modification tracking for a scope after a successful write."""
        match scope:
            case SCOPE.GLOBAL:
                self.modified_fields.clear()
                self.modified_nested_fields.clear()
            case SCOPE.PROJECT:
                self.modified_project_fields.clear()
                self.modified_project_nested_fields.clear()

    def _enqueue_write(self, scope: SCOPE, task: Callable[..., None]):
        """Chain an async write task so concurrent saves are serialised and never interleave."""
        import asyncio
        prev = self._write_queue

        async def chained() -> None:
            if prev is not None:
                try:
                    await prev
                except Exception:
                    pass
            try:
                task()
                self._clear_modified_scope(scope)
            except Exception as e:
                self._record_error(scope, e)

        self._write_queue = asyncio.create_task(chained())

    def _persist_scoped_settings(
        self,
        scope: SCOPE,
        snapshot_settings: Settings,
        modified_fields: Set[str],
        modified_nested_fields: Dict[str, Set[str]],
    ):
        """Write only the modified fields back to storage, merging at the key level to preserve concurrent changes."""
        def persist_fn(current):
            current_dict = json.loads(current) if current else {}
            snapshot_dict = asdict(snapshot_settings)
            merged = dict(current_dict)
            for field_name in modified_fields:
                value = snapshot_dict.get(field_name)
                if field_name in modified_nested_fields and isinstance(value, dict):
                    base = current_dict.get(field_name) or {}
                    if isinstance(base, dict):
                        merged_nested = {**base}
                        for nested_key in modified_nested_fields[field_name]:
                            merged_nested[nested_key] = value.get(nested_key)
                        merged[field_name] = merged_nested
                    else:
                        merged[field_name] = value
                else:
                    merged[field_name] = value
            return LockResult(result=None, next=json.dumps(merged, indent=2, default=str))

        self.storage.with_lock(scope, persist_fn)

    def _save(self):
        """Update the merged view and enqueue an async write of modified global settings."""
        self.settings = self._deep_merge_settings(self.global_settings, self.project_settings)
        if self.global_settings_load_error:
            return
        snapshot_global = copy.deepcopy(self.global_settings)
        modified_fields = set(self.modified_fields)
        modified_nested_fields = self._clone_modified_nested_fields(self.modified_nested_fields)

        def write_task():
            self._persist_scoped_settings(SCOPE.GLOBAL, snapshot_global, modified_fields, modified_nested_fields)

        self._enqueue_write(SCOPE.GLOBAL, write_task)

    def _save_project_settings(self, settings: Settings):
        """Update the merged view and enqueue an async write of modified project settings."""
        self.project_settings = copy.deepcopy(settings)
        self.settings = self._deep_merge_settings(self.global_settings, self.project_settings)
        if self.project_settings_load_error:
            return
        snapshot_project = copy.deepcopy(self.project_settings)
        modified_fields = set(self.modified_project_fields)
        modified_nested_fields = self._clone_modified_nested_fields(self.modified_project_nested_fields)

        def write_task():
            self._persist_scoped_settings(SCOPE.PROJECT, snapshot_project, modified_fields, modified_nested_fields)

        self._enqueue_write(SCOPE.PROJECT, write_task)

    async def flush(self) -> None:
        """Wait for any pending async writes to complete."""
        if self._write_queue is not None:
            await self._write_queue

    def drain_errors(self) -> List[SettingsError]:
        """Return and clear all accumulated load and write errors."""
        drained = self.errors.copy()
        self.errors.clear()
        return drained

    async def reload(self) -> None:
        """Flush pending writes, reload both scopes from storage, and recompute the merged view."""
        await self.flush()
        global_load = SettingsManager._try_load_from_storage(self.storage, SCOPE.GLOBAL)
        if not global_load[1]:
            self.global_settings = global_load[0]
            self.global_settings_load_error = None
        else:
            self.global_settings_load_error = global_load[1]
            self._record_error(SCOPE.GLOBAL, global_load[1])

        self.modified_fields.clear()
        self.modified_nested_fields.clear()
        self.modified_project_fields.clear()
        self.modified_project_nested_fields.clear()

        project_load = SettingsManager._try_load_from_storage(self.storage, SCOPE.PROJECT)
        if not project_load[1]:
            self.project_settings = project_load[0]
            self.project_settings_load_error = None
        else:
            self.project_settings_load_error = project_load[1]
            self._record_error(SCOPE.PROJECT, project_load[1])

        self.settings = self._deep_merge_settings(self.global_settings, self.project_settings)

    def apply_overrides(self, overrides: Dict[str, Any]):
        """Apply runtime overrides on top of the current merged settings without persisting."""
        override_settings = SettingsManager._settings_from_dict(overrides)
        self.settings = self._deep_merge_settings(self.settings, override_settings)

    def get_global_settings(self) -> Settings:
        """Return a deep copy of the raw global settings (before project merge)."""
        return copy.deepcopy(self.global_settings)

    def get_project_settings(self) -> Settings:
        """Return a deep copy of the raw project settings (before global merge)."""
        return copy.deepcopy(self.project_settings)

    def get_default_provider(self) -> Optional[str]:
        """Return the default LLM provider, or None if unset."""
        return self.settings.default_provider

    def set_default_provider(self, provider: str):
        """Set the default LLM provider and persist to global settings."""
        self.global_settings.default_provider = provider
        self._mark_modified("default_provider")
        self._save()

    def get_default_model(self) -> Optional[str]:
        """Return the default model ID, or None if unset."""
        return self.settings.default_model

    def set_default_model(self, model: str):
        """Set the default model ID and persist to global settings."""
        self.global_settings.default_model = model
        self._mark_modified("default_model")
        self._save()

    def set_default_model_and_provider(self, provider: str, model_id: str):
        """Set both the default provider and model in a single write."""
        self.global_settings.default_provider = provider
        self.global_settings.default_model = model_id
        self._mark_modified("default_provider")
        self._mark_modified("default_model")
        self._save()

    def get_default_thinking_level(self) -> Optional[ThinkingLevel]:
        """Return the default thinking level, or None if unset."""
        return self.settings.default_thinking_level

    def set_default_thinking_level(self, level: ThinkingLevel):
        """Set the default thinking level and persist to global settings."""
        self.global_settings.default_thinking_level = level
        self._mark_modified("default_thinking_level")
        self._save()

    def get_transport(self) -> Transport:
        """Return the configured transport, defaulting to Transport.Auto."""
        return self.settings.transport or Transport.Auto

    def set_transport(self, transport: Transport):
        """Set the transport and persist to global settings."""
        self.global_settings.transport = transport
        self._mark_modified("transport")
        self._save()

    def get_compaction_settings(self) -> dict:
        """Return all resolved compaction settings with defaults applied."""
        return {
            "enabled": self.get_compaction_enabled(),
            "reserve_tokens": self.get_compaction_reserve_tokens(),
            "keep_recent_tokens": self.get_compaction_keep_recent_tokens(),
        }

    def get_compaction_enabled(self) -> bool:
        """Return whether compaction is enabled (default: True)."""
        c = self.settings.compaction
        if c is None or c.enabled is None:
            return True
        return c.enabled

    def set_compaction_enabled(self, enabled: bool):
        """Enable or disable compaction and persist to global settings."""
        if not self.global_settings.compaction:
            self.global_settings.compaction = CompactionSettings()
        self.global_settings.compaction.enabled = enabled
        self._mark_modified("compaction", "enabled")
        self._save()

    def get_compaction_reserve_tokens(self) -> int:
        """Return the token budget reserved for the compaction prompt and response (default: 16384)."""
        c = self.settings.compaction
        return c.reserve_tokens if c and c.reserve_tokens is not None else 16384

    def get_compaction_keep_recent_tokens(self) -> int:
        """Return the number of recent tokens to keep uncompacted (default: 20000)."""
        c = self.settings.compaction
        return c.keep_recent_tokens if c and c.keep_recent_tokens is not None else 20000

    def get_branch_summary_settings(self) -> dict:
        """Return all resolved branch summary settings with defaults applied."""
        return {
            "reserve_tokens": self.get_branch_summary_reserve_tokens(),
            "skip_prompt": self.get_branch_summary_skip_prompt(),
        }

    def get_branch_summary_reserve_tokens(self) -> int:
        """Return the token budget reserved for branch summarisation (default: 16384)."""
        b = self.settings.branch_summary
        return b.reserve_tokens if b and b.reserve_tokens is not None else 16384

    def get_branch_summary_skip_prompt(self) -> bool:
        """Return whether the branch summary confirmation prompt is skipped (default: False)."""
        b = self.settings.branch_summary
        return b.skip_prompt if b and b.skip_prompt is not None else False

    def get_retry_settings(self) -> dict:
        """Return all resolved retry settings with defaults applied."""
        return {
            "enabled": self.get_retry_enabled(),
            "max_retries": self.get_retry_max_retries(),
            "base_delay_ms": self.get_retry_base_delay_ms(),
        }

    def get_retry_enabled(self) -> bool:
        """Return whether request retries are enabled (default: True)."""
        r = self.settings.retry
        return r.enabled if r and r.enabled is not None else True

    def set_retry_enabled(self, enabled: bool):
        """Enable or disable request retries and persist to global settings."""
        if not self.global_settings.retry:
            self.global_settings.retry = RetrySettings()
        self.global_settings.retry.enabled = enabled
        self._mark_modified("retry", "enabled")
        self._save()

    def get_retry_max_retries(self) -> int:
        """Return the maximum number of retry attempts (default: 3)."""
        r = self.settings.retry
        return r.max_retries if r and r.max_retries is not None else 3

    def get_retry_base_delay_ms(self) -> int:
        """Return the base delay in ms for exponential backoff (default: 2000)."""
        r = self.settings.retry
        return r.base_delay_ms if r and r.base_delay_ms is not None else 2000

    def get_packages(self) -> list:
        """Return the list of registered package sources."""
        return self.settings.packages or []

    def set_packages(self, packages: list):
        """Set the package sources and persist to global settings."""
        self.global_settings.packages = packages
        self._mark_modified("packages")
        self._save()

    def get_extension_paths(self) -> list[str]:
        """Return the list of local extension file paths."""
        return self.settings.extensions or []

    def set_extension_paths(self, paths: list[str]):
        """Set the extension paths and persist to global settings."""
        self.global_settings.extensions = paths
        self._mark_modified("extensions")
        self._save()

    def get_skill_paths(self) -> list[str]:
        """Return the list of local skill file paths."""
        return self.settings.skills or []

    def set_skill_paths(self, paths: list[str]):
        """Set the skill paths and persist to global settings."""
        self.global_settings.skills = paths
        self._mark_modified("skills")
        self._save()

    def get_prompt_paths(self) -> list[str]:
        """Return the list of local prompt template paths."""
        return self.settings.prompts or []

    def set_prompt_paths(self, paths: list[str]):
        """Set the prompt template paths and persist to global settings."""
        self.global_settings.prompts = paths
        self._mark_modified("prompts")
        self._save()

    def get_steering_mode(self) -> SteeringMode:
        """Return the steering mode, defaulting to SteeringMode.OneAtATime."""
        return self.settings.steering_mode or SteeringMode.OneAtATime

    def set_steering_mode(self, mode: SteeringMode):
        """Set the steering mode and persist to global settings."""
        self.global_settings.steering_mode = mode
        self._mark_modified("steering_mode")
        self._save()

    def get_follow_up_mode(self) -> FollowupMode:
        """Return the follow-up mode, defaulting to FollowupMode.OneAtATime."""
        return self.settings.follow_up_mode or FollowupMode.OneAtATime

    def set_follow_up_mode(self, mode: FollowupMode):
        """Set the follow-up mode and persist to global settings."""
        self.global_settings.follow_up_mode = mode
        self._mark_modified("follow_up_mode")
        self._save()

    def get_enable_skill_commands(self) -> bool:
        """Return whether skill slash commands are enabled (default: True)."""
        return self.settings.enable_skill_commands if self.settings.enable_skill_commands is not None else True

    def set_enable_skill_commands(self, enabled: bool):
        """Enable or disable skill slash commands and persist to global settings."""
        self.global_settings.enable_skill_commands = enabled
        self._mark_modified("enable_skill_commands")
        self._save()

    def get_enabled_models(self) -> list[str] | None:
        """Return the model filter patterns, or None if all models are enabled."""
        return self.settings.enabled_models

    def set_enabled_models(self, patterns: list[str] | None):
        """Set the model filter patterns and persist to global settings."""
        self.global_settings.enabled_models = patterns
        self._mark_modified("enabled_models")
        self._save()

    def get_session_dir(self) -> Path | None:
        """Return the resolved session storage directory, expanding ~ if present."""
        session_dir = self.settings.session_dir
        if session_dir is None:
            return None
        if session_dir == "~":
            return Path.home()
        if session_dir.startswith("~/"):
            return Path.home() / session_dir[2:]
        return Path(session_dir).resolve()

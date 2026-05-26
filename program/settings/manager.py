from __future__ import annotations
import copy
import dataclasses as dc
import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Set, Dict, List, Any, Callable, cast

from program.settings.storage import SettingsStorage, FileSettingsStorage, InMemorySettingsStorage, LockResult
from program.settings.types import (
    Settings, SCOPE, SettingsError,
    CompactionSettings, BranchSummarySettings,
    RetrySettings, ProviderRetrySettings, ThinkingBudgetsSettings,
    ImageSettings, STTSettings, TTSSettings, MemorySettings, ExtensionEntry,
    AuxiliarySettings, AuxiliaryTaskSettings,
)
from program.gateway.channels.types import (
    ChannelsSettings,
    WebSocketChannelConfig, TelegramChannelConfig, DiscordChannelConfig,
    SlackChannelConfig, TwitchChannelConfig,
)
from program.acp.types import ACPSettings
from program.engine.types import SteeringMode, FollowupMode
from program.inference.types import Transport, ThinkingLevel

_NESTED_FIELD_TYPES: dict[str, type] = {
    'compaction': CompactionSettings,
    'branch_summary': BranchSummarySettings,
    'thinking_budgets': ThinkingBudgetsSettings,
    'image': ImageSettings,
    'stt': STTSettings,
    'tts': TTSSettings,
    'memory': MemorySettings,
}

# Pydantic BaseModel fields — use model_validate() instead of **kwargs
_PYDANTIC_FIELD_TYPES: dict[str, type] = {
    'channels': ChannelsSettings,
    'acp': ACPSettings,
}


class SettingsManager:
    def __init__(
        self,
        storage: SettingsStorage,
        initial_global: Settings,
        initial_project: Settings,
        global_load_error: Optional[Exception] = None,
        project_load_error: Optional[Exception] = None,
        initial_errors: Optional[List[SettingsError]] = None,
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
    def create(cwd: Path, config_dir: Optional[Path] = None) -> SettingsManager:
        """Create a SettingsManager backed by files in cwd (and optional config_dir for global settings)."""
        storage = FileSettingsStorage(cwd, config_dir)
        return SettingsManager.from_storage(storage)

    @staticmethod
    def from_storage(storage: SettingsStorage) -> SettingsManager:
        """Create a SettingsManager from an arbitrary storage backend."""
        global_settings, global_error = SettingsManager._try_load_from_storage(storage, SCOPE.GLOBAL)
        project_settings, project_error = SettingsManager._try_load_from_storage(storage, SCOPE.PROJECT)
        initial_errors = []
        if global_error:
            initial_errors.append(SettingsError(scope=SCOPE.GLOBAL, error=global_error))
        if project_error:
            initial_errors.append(SettingsError(scope=SCOPE.PROJECT, error=project_error))
        return SettingsManager(storage, global_settings, project_settings,
                global_error, project_error, initial_errors)

    @staticmethod
    def in_memory(settings: Optional[Dict[str, Any]] = None) -> SettingsManager:
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
            if key in _PYDANTIC_FIELD_TYPES and isinstance(value, dict):
                kwargs[key] = _PYDANTIC_FIELD_TYPES[key].model_validate(value)
            elif key in _NESTED_FIELD_TYPES and isinstance(value, dict):
                nested_cls = _NESTED_FIELD_TYPES[key]
                valid_nested = {f.name for f in dc.fields(nested_cls)}
                kwargs[key] = nested_cls(**{k: v for k, v in value.items() if k in valid_nested})
            elif key == 'extension_list' and isinstance(value, list):
                valid_fields = {f.name for f in dc.fields(ExtensionEntry)}
                entries = []
                for item in value:
                    if isinstance(item, dict):
                        entries.append(ExtensionEntry(**{k: v for k, v in item.items() if k in valid_fields}))
                kwargs[key] = entries
            elif key == 'retry' and isinstance(value, dict):
                provider = value.get('provider')
                if isinstance(provider, dict):
                    valid_provider = {f.name for f in dc.fields(ProviderRetrySettings)}
                    provider = ProviderRetrySettings(**{k: v for k, v in provider.items() if k in valid_provider})
                valid_retry = {f.name for f in dc.fields(RetrySettings)}
                retry_kwargs = {k: v for k, v in value.items() if k in valid_retry and k != 'provider'}
                kwargs[key] = RetrySettings(**retry_kwargs, provider=provider)
            elif key == 'auxiliary' and isinstance(value, dict):
                valid_task = {f.name for f in dc.fields(AuxiliaryTaskSettings)}
                valid_aux = {f.name for f in dc.fields(AuxiliarySettings)}
                aux_kwargs: dict[str, Any] = {}
                for slot, slot_val in value.items():
                    if slot in valid_aux and isinstance(slot_val, dict):
                        aux_kwargs[slot] = AuxiliaryTaskSettings(**{k: v for k, v in slot_val.items() if k in valid_task})
                kwargs[key] = AuxiliarySettings(**aux_kwargs)
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

    @staticmethod
    def _to_json_dict(settings: Settings) -> dict:
        """Convert a Settings dataclass to a JSON-serializable dict.

        Handles nested dataclasses via dataclasses.asdict and Pydantic BaseModel
        fields via model_dump(), so mixed-type settings serialize correctly.
        """
        result = {}
        for f in dc.fields(settings):
            val = getattr(settings, f.name)
            if val is None:
                result[f.name] = None
            elif dc.is_dataclass(val):
                result[f.name] = dc.asdict(cast(Any, val))
            elif hasattr(val, 'model_dump'):
                result[f.name] = val.model_dump()
            else:
                result[f.name] = val
        return result

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
            snapshot_dict = SettingsManager._to_json_dict(snapshot_settings)
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

    def unset_default_provider(self):
        """Unset the default LLM provider and persist to global settings."""
        self.global_settings.default_provider = None
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

    def unset_default_model(self):
        """Unset the default model ID and persist to global settings."""
        self.global_settings.default_model = None
        self._mark_modified("default_model")
        self._save()

    def set_default_model_and_provider(self, provider: str, model_id: str):
        """Set both the default provider and model in a single write."""
        self.global_settings.default_provider = provider
        self.global_settings.default_model = model_id
        self._mark_modified("default_provider")
        self._mark_modified("default_model")
        self._save()

    def set_default_model_settings(
        self,
        model: str | None,
        provider: str | None,
    ):
        """Set default model/provider values when provided and persist them."""
        if model is not None and provider is not None:
            self.set_default_model_and_provider(provider, model)
        elif model is not None:
            self.set_default_model(model)
        elif provider is not None:
            self.set_default_provider(provider)

    def unset_default_model_and_provider(self):
        """Unset both the default provider and model in a single write."""
        self.global_settings.default_provider = None
        self.global_settings.default_model = None
        self._mark_modified("default_provider")
        self._mark_modified("default_model")
        self._save()

    def unset_default_model_settings(
        self,
        model: bool,
        provider: bool,
    ):
        """Unset default model/provider values when requested and persist them."""
        if model and provider:
            self.unset_default_model_and_provider()
        elif model:
            self.unset_default_model()
        elif provider:
            self.unset_default_provider()

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
            "strategy": self.get_compaction_strategy(),
        }

    def get_compaction_enabled(self) -> bool:
        """Return whether compaction is globally enabled (default: True)."""
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

    def get_compaction_strategy(self) -> str:
        """Return the active compaction strategy name (default: 'summarization')."""
        c = self.settings.compaction
        return c.strategy if c and c.strategy else "summarization"

    def _get_strategy_raw(self, name: str) -> dict:
        """Return the raw settings dict for a named strategy (empty dict if unset)."""
        c = self.settings.compaction
        strategies: dict = (c.strategies or {}) if c else {}
        return strategies.get(name) or {}

    def get_compaction_summarization_settings(self) -> dict:
        """Return SummarizationCompaction settings merged with defaults."""
        raw = self._get_strategy_raw("summarization")
        return {
            "enabled": raw.get("enabled", True),
            "reserve_tokens": raw.get("reserve_tokens", 16384),
            "keep_recent_tokens": raw.get("keep_recent_tokens", 20000),
        }

    def get_compaction_sliding_window_settings(self) -> dict:
        """Return SlidingWindowCompaction settings merged with defaults."""
        raw = self._get_strategy_raw("sliding_window")
        return {
            "enabled": raw.get("enabled", True),
            "trigger_percent": raw.get("trigger_percent", 0.5),
            "batch_tokens": raw.get("batch_tokens", 10_000),
            "keep_recent_tokens": raw.get("keep_recent_tokens", 20000),
        }

    def get_compaction_lcm_settings(self) -> dict:
        """Return LCMCompaction settings merged with defaults."""
        raw = self._get_strategy_raw("lcm")
        return {
            "enabled": raw.get("enabled", True),
            "reserve_tokens": raw.get("reserve_tokens", 16384),
            "keep_recent_tokens": raw.get("keep_recent_tokens", 20000),
            "condense_threshold": raw.get("condense_threshold", 4),
            "max_depth": raw.get("max_depth", 3),
            "db_path": raw.get("db_path", None),
        }

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

    def is_extensions_enabled(self) -> bool:
        """Return whether extensions are globally enabled (default True)."""
        return self.settings.extensions if self.settings.extensions is not None else True

    def set_extensions_enabled(self, enabled: bool):
        """Set the global extension toggle and persist to global settings."""
        self.global_settings.extensions = enabled
        self._mark_modified("extensions")
        self._save()

    def get_extension_list(self) -> list[ExtensionEntry]:
        """Return the list of per-extension config entries."""
        return self.settings.extension_list or []

    def set_extension_list(self, entries: list[ExtensionEntry]):
        """Set the per-extension config entries and persist to global settings."""
        self.global_settings.extension_list = entries
        self._mark_modified("extension_list")
        self._save()

    def get_extension_paths(self) -> list[str]:
        """Return extension paths from the per-extension config entries."""
        return [entry.path for entry in self.get_extension_list()]

    def set_extension_paths(self, paths: list[str]):
        """Set extension paths, preserving the current extension-list storage shape."""
        self.set_extension_list([ExtensionEntry(path=path) for path in paths])

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

    def get_mcp_servers(self) -> list:
        """Return list of MCPServerConfig objects parsed from settings, or empty list."""
        from program.mcp.types import MCPServerConfig
        raw = self.settings.mcp_servers or []
        result = []
        for entry in raw:
            if not isinstance(entry, dict) or 'name' not in entry:
                continue
            result.append(MCPServerConfig(
                name=entry['name'],
                transport=entry.get('transport', 'stdio'),
                command=entry.get('command'),
                args=entry.get('args', []),
                url=entry.get('url'),
                env=entry.get('env', {}),
                auth_token=entry.get('auth_token'),
            ))
        return result

    def get_acp_agents(self) -> list:
        """Return enabled ACPAgentConfig entries, or empty list if acp is disabled."""
        acp = self.settings.acp
        if acp is None or not acp.enabled:
            return []
        return [a for a in acp.agents if a.enabled]

    def get_acp_settings(self):
        """Return the resolved ACPSettings, with defaults when unset."""
        return self.settings.acp or ACPSettings()

    def get_acp_agent_config(self, name: str):
        """Return the ACPAgentConfig for the named agent, or None if not found."""
        acp = self.settings.acp
        if acp is None:
            return None
        return next((a for a in acp.agents if a.name == name), None)

    def _get_or_init_acp(self):
        if self.global_settings.acp is None:
            self.global_settings.acp = ACPSettings()
        return self.global_settings.acp

    def set_acp_enabled(self, enabled: bool) -> None:
        self._get_or_init_acp().enabled = enabled
        self._mark_modified('acp')
        self._save()

    def set_acp_agent_config(self, name: str, **kwargs) -> None:
        """Upsert an agent entry by name. Pass fields to create or update."""
        from program.acp.types import ACPAgentConfig
        acp = self._get_or_init_acp()
        existing = next((a for a in acp.agents if a.name == name), None)
        if existing is None:
            acp.agents.append(ACPAgentConfig(name=name, **kwargs))
        else:
            updated = existing.model_dump()
            updated.update(kwargs)
            acp.agents[acp.agents.index(existing)] = ACPAgentConfig(**updated)
        self._mark_modified('acp')
        self._save()

    def remove_acp_agent_config(self, name: str) -> bool:
        """Remove an agent entry by name. Returns True if it existed."""
        acp = self.settings.acp
        if acp is None:
            return False
        before = len(acp.agents)
        acp.agents = [a for a in acp.agents if a.name != name]
        if len(acp.agents) == before:
            return False
        self._mark_modified('acp')
        self._save()
        return True

    def get_cron_enabled(self) -> bool:
        """Return whether the cron scheduler is enabled (default: True)."""
        return self.settings.cron_enabled if self.settings.cron_enabled is not None else True

    def set_cron_enabled(self, enabled: bool):
        """Enable or disable the cron scheduler and persist to global settings."""
        self.global_settings.cron_enabled = enabled
        self._mark_modified("cron_enabled")
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

    def set_session_dir(self, path: str | None):
        """Set the session storage directory and persist to global settings."""
        self.global_settings.session_dir = path
        self._mark_modified("session_dir")
        self._save()

    # ── Image ─────────────────────────────────────────────────────────────────

    def get_image_auto_resize(self) -> bool:
        """Return whether images are auto-resized to 2000×2000 before being sent to the LLM (default: True)."""
        i = self.settings.image
        return i.auto_resize if i and i.auto_resize is not None else True

    def set_image_auto_resize(self, enabled: bool):
        if not self.global_settings.image:
            self.global_settings.image = ImageSettings()
        self.global_settings.image.auto_resize = enabled
        self._mark_modified("image", "auto_resize")
        self._save()

    def get_image_block_images(self) -> bool:
        """Return whether sending images to the LLM is blocked entirely (default: False)."""
        i = self.settings.image
        return i.block_images if i and i.block_images is not None else False

    def set_image_block_images(self, enabled: bool):
        if not self.global_settings.image:
            self.global_settings.image = ImageSettings()
        self.global_settings.image.block_images = enabled
        self._mark_modified("image", "block_images")
        self._save()

    # ── Execution ─────────────────────────────────────────────────────────────

    def get_execute_path(self) -> str | None:
        """Return the custom execute path, or None to use the system default."""
        return self.settings.execute_path

    def set_execute_path(self, path: str | None):
        self.global_settings.execute_path = path
        self._mark_modified("execute_path")
        self._save()

    def get_shell_path(self) -> str | None:
        """Compatibility alias for the configured execute path."""
        return self.get_execute_path()

    def set_shell_path(self, path: str | None):
        """Compatibility alias for the configured execute path."""
        self.set_execute_path(path)

    def get_execute_command_prefix(self) -> str | None:
        """Return the prefix prepended to every execute command, or None if unset."""
        return self.settings.execute_command_prefix

    def set_execute_command_prefix(self, prefix: str | None):
        self.global_settings.execute_command_prefix = prefix
        self._mark_modified("execute_command_prefix")
        self._save()

    def get_shell_command_prefix(self) -> str | None:
        """Compatibility alias for the execute command prefix."""
        return self.get_execute_command_prefix()

    def set_shell_command_prefix(self, prefix: str | None):
        """Compatibility alias for the execute command prefix."""
        self.set_execute_command_prefix(prefix)

    # ── Channels ──────────────────────────────────────────────────────────────

    def get_channels_settings(self) -> ChannelsSettings:
        """Return the resolved channels settings. Missing keys use per-channel defaults."""
        if self.settings.channels is None:
            return ChannelsSettings()
        return self.settings.channels

    def get_websocket_channel_config(self) -> WebSocketChannelConfig:
        return self.get_channels_settings().websocket

    def get_telegram_channel_config(self) -> TelegramChannelConfig:
        return self.get_channels_settings().telegram

    def get_discord_channel_config(self) -> DiscordChannelConfig:
        return self.get_channels_settings().discord

    def get_slack_channel_config(self) -> SlackChannelConfig:
        return self.get_channels_settings().slack

    def get_twitch_channel_config(self) -> TwitchChannelConfig:
        return self.get_channels_settings().twitch

    def _get_or_init_channels(self) -> ChannelsSettings:
        if self.global_settings.channels is None:
            self.global_settings.channels = ChannelsSettings()
        return self.global_settings.channels

    def set_websocket_channel_config(self, **kwargs) -> None:
        ch = self._get_or_init_channels()
        ch.websocket = WebSocketChannelConfig(**{**ch.websocket.model_dump(), **kwargs})
        self._mark_modified('channels')
        self._save()

    def set_telegram_channel_config(self, **kwargs) -> None:
        ch = self._get_or_init_channels()
        ch.telegram = TelegramChannelConfig(**{**ch.telegram.model_dump(), **kwargs})
        self._mark_modified('channels')
        self._save()

    def set_discord_channel_config(self, **kwargs) -> None:
        ch = self._get_or_init_channels()
        ch.discord = DiscordChannelConfig(**{**ch.discord.model_dump(), **kwargs})
        self._mark_modified('channels')
        self._save()

    def set_slack_channel_config(self, **kwargs) -> None:
        ch = self._get_or_init_channels()
        ch.slack = SlackChannelConfig(**{**ch.slack.model_dump(), **kwargs})
        self._mark_modified('channels')
        self._save()

    def set_twitch_channel_config(self, **kwargs) -> None:
        ch = self._get_or_init_channels()
        ch.twitch = TwitchChannelConfig(**{**ch.twitch.model_dump(), **kwargs})
        self._mark_modified('channels')
        self._save()

    # ── STT / TTS ─────────────────────────────────────────────────────────────

    def get_stt_settings(self) -> STTSettings:
        """Return the resolved STT settings, with empty defaults when unset."""
        return self.settings.stt or STTSettings()

    def get_tts_settings(self) -> TTSSettings:
        """Return the resolved TTS settings, with empty defaults when unset."""
        return self.settings.tts or TTSSettings()

    # ── Auxiliary models ──────────────────────────────────────────────────────

    def get_auxiliary_task(self, slot: str) -> AuxiliaryTaskSettings:
        """Return model/provider for a named auxiliary task slot. Returns empty defaults when unset."""
        aux = self.settings.auxiliary
        if aux is None:
            return AuxiliaryTaskSettings()
        return getattr(aux, slot, None) or AuxiliaryTaskSettings()

    # ── Memory ────────────────────────────────────────────────────────────────

    def get_memory_settings(self) -> MemorySettings:
        """Return resolved memory settings, with defaults when unset."""
        return self.settings.memory or MemorySettings()

    def set_memory_settings(self, **kwargs) -> None:
        current = self.global_settings.memory or MemorySettings()
        values = asdict(current)
        values.update(kwargs)
        self.global_settings.memory = MemorySettings(**values)
        self._mark_modified('memory')
        self._save()

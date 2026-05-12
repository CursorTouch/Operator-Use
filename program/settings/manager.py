from __future__ import annotations
from program.settings.storage import SettingsStorage, FileSettingsStorage, InMemorySettingsStorage, LockResult  
from program.settings.types import Settings, SCOPE, SettingsError  
from typing import Optional, Set, Dict, List, Any, Callable
from dataclasses import asdict
from pathlib import Path  
import copy  
import json  
import asyncio  
from dataclasses import field  
  
class SettingsManager:
    # Manages layered settings: global (user-wide) and project (repo-specific).
    # Handles async persistence, tracks which fields changed, and prevents concurrent writes.
    def __init__(self, storage: SettingsStorage, initial_global: Settings, initial_project: Settings,
                 global_load_error: Optional[Exception] = None,
                 project_load_error: Optional[Exception] = None,
                 initial_errors: List[SettingsError] = None):
        self.storage = storage
        self.global_settings = initial_global
        self.project_settings = initial_project
        self.settings = self._deep_merge_settings(initial_global, initial_project)
        # Track which fields were modified to persist only changes (not entire file)
        self.modified_fields: Set[str] = set()
        self.modified_nested_fields: Dict[str, Set[str]] = {}
        self.modified_project_fields: Set[str] = set()
        self.modified_project_nested_fields: Dict[str, Set[str]] = {}
        # Preserve load errors to prevent writes if file was corrupt
        self.global_settings_load_error: Optional[Exception] = global_load_error
        self.project_settings_load_error: Optional[Exception] = project_load_error
        self.errors: List[SettingsError] = initial_errors.copy() if initial_errors else []
        # Queue ensures writes are serialized even if multiple setters called quickly
        self._write_queue: Optional[asyncio.Task] = None  
        
    @staticmethod
    def create(cwd: Path, agent_dir: Optional[Path] = None) -> 'SettingsManager':
        # Load settings from files in cwd (and optional agent_dir for project settings)
        storage = FileSettingsStorage(cwd, agent_dir)
        return SettingsManager.from_storage(storage)

    @staticmethod
    def from_storage(storage: SettingsStorage) -> 'SettingsManager':
        # Load both scopes independently; preserve load errors to gate future writes
        global_load = SettingsManager._try_load_from_storage(storage, "global")
        project_load = SettingsManager._try_load_from_storage(storage, "project")
        initial_errors = []
        if global_load[1]:
            initial_errors.append(SettingsError(scope="global", error=global_load[1]))
        if project_load[1]:
            initial_errors.append(SettingsError(scope="project", error=project_load[1]))
        return SettingsManager(storage, global_load[0], project_load[0],
                               global_load[1], project_load[1], initial_errors)

    @staticmethod
    def in_memory(settings: Dict = None) -> 'SettingsManager':
        # Test helper: in-memory storage with optional seed data
        storage = InMemorySettingsStorage()
        settings_dict = settings or {}
        storage.with_lock("global", lambda _: LockResult(result=None, next=json.dumps(settings_dict, indent=2)))
        return SettingsManager.from_storage(storage)    
        
    @staticmethod
    def _load_from_storage(storage: SettingsStorage, scope: SCOPE) -> Settings:
        # Acquires file lock, parses JSON, returns Settings object
        def load_fn(current):
            if not current:
                return LockResult(result=Settings(), next=None)
            data = json.loads(current)
            return LockResult(result=Settings(**data), next=None)
        return storage.with_lock(scope, load_fn).result

    @staticmethod
    def _try_load_from_storage(storage: SettingsStorage, scope: SCOPE) -> tuple[Settings, Optional[Exception]]:
        # Wraps load in try/except: returns (Settings, None) on success or (empty Settings, error) on failure
        try:
            return (SettingsManager._load_from_storage(storage, scope), None)
        except Exception as e:
            return (Settings(), e)    
        
    def _deep_merge_settings(self, global_settings: Settings, project_settings: Settings) -> Settings:
        # Project-level settings override global ones; nested dicts are merged at key level
        merged = copy.deepcopy(global_settings)
        for key, value in project_settings.__dict__.items():
            if value is not None:
                if isinstance(value, dict) and getattr(merged, key) is not None:
                    existing = getattr(merged, key)
                    if isinstance(existing, dict):
                        merged_dict = {**existing, **value}
                        setattr(merged, key, merged_dict)
                    else:
                        setattr(merged, key, value)
                else:
                    setattr(merged, key, value)
        return merged

    def _mark_modified(self, field: str, nested_field: Optional[str] = None):
        # Track which global settings fields changed (to persist only those fields)
        self.modified_fields.add(field)
        if nested_field:
            if field not in self.modified_nested_fields:
                self.modified_nested_fields[field] = set()
            self.modified_nested_fields[field].add(nested_field)

    def _mark_project_modified(self, field: str, nested_field: Optional[str] = None):
        # Track which project settings fields changed
        self.modified_project_fields.add(field)
        if nested_field:
            if field not in self.modified_project_nested_fields:
                self.modified_project_nested_fields[field] = set()
            self.modified_project_nested_fields[field].add(nested_field)

    def _clone_modified_nested_fields(self, source: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
        # Creates a snapshot of nested field tracking so async write sees state at enqueue time
        snapshot = {}
        for key, value in source.items():
            snapshot[key] = set(value)
        return snapshot  
  
    def _record_error(self, scope: SCOPE, error: Exception):
        # Appends error to queue for later retrieval (caller can drain_errors() after operations)
        self.errors.append(SettingsError(scope=scope, error=error))

    def _clear_modified_scope(self, scope: SCOPE):
        # Resets tracking after successful write (so next change is detected)
        match scope:
            case "global":
                self.modified_fields.clear()
                self.modified_nested_fields.clear()
            case "project":
                self.modified_project_fields.clear()
                self.modified_project_nested_fields.clear()

    def _enqueue_write(self, scope: SCOPE, task: Callable[..., None]):
        # Chains async writes to prevent concurrent file mutations; executes task and clears tracking
        prev = self._write_queue

        async def chained():
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
        
    def _persist_scoped_settings(self, scope: SCOPE, snapshot_settings: Settings, modified_fields: Set[str], modified_nested_fields: Dict[str, Set[str]]):
        # Merges only changed fields into file state (preserves other fields written concurrently)
        # For nested dicts: merges at key level to avoid clobbering sibling keys
        def persist_fn(current):
            current_file_settings = Settings()
            if current:
                current_file_settings = Settings(**json.loads(current))

            merged_settings = copy.deepcopy(current_file_settings)

            for field in modified_fields:
                value = getattr(snapshot_settings, field)
                if field in modified_nested_fields and isinstance(value, dict):
                    # For nested dicts, merge only the keys we modified
                    base_nested = getattr(current_file_settings, field) or {}
                    in_memory_nested = value
                    merged_nested = {**base_nested}
                    for nested_key in modified_nested_fields[field]:
                        merged_nested[nested_key] = in_memory_nested.get(nested_key)
                    setattr(merged_settings, field, merged_nested)
                else:
                    setattr(merged_settings, field, value)

            return LockResult(result=None, next=json.dumps(asdict(merged_settings), indent=2, default=str))

        self.storage.with_lock(scope, persist_fn)    
        
    def _save(self):
        # Updates merged view, then enqueues async write of global settings
        # Snapshots state to prevent writes from seeing later mutations
        self.settings = self._deep_merge_settings(self.global_settings, self.project_settings)

        # Skip write if global file was corrupt (load error prevents writes to avoid data loss)
        if self.global_settings_load_error:
            return

        # Capture state at enqueue time (protects against mutations before async write runs)
        snapshot_global = copy.deepcopy(self.global_settings)
        modified_fields = set(self.modified_fields)
        modified_nested_fields = self._clone_modified_nested_fields(self.modified_nested_fields)

        def write_task():
            self._persist_scoped_settings("global", snapshot_global, modified_fields, modified_nested_fields)

        self._enqueue_write("global", write_task)

    def _save_project_settings(self, settings: Settings):
        # Similar to _save but for project-scoped settings (called with new Settings object)
        self.project_settings = copy.deepcopy(settings)
        self.settings = self._deep_merge_settings(self.global_settings, self.project_settings)

        if self.project_settings_load_error:
            return

        snapshot_project = copy.deepcopy(self.project_settings)
        modified_fields = set(self.modified_project_fields)
        modified_nested_fields = self._clone_modified_nested_fields(self.modified_project_nested_fields)

        def write_task():
            self._persist_scoped_settings("project", snapshot_project, modified_fields, modified_nested_fields)

        self._enqueue_write("project", write_task)  
  
    async def flush(self):
        # Waits for any pending async writes to complete (use before reload to ensure consistency)
        if self._write_queue is not None:
            await self._write_queue

    def drain_errors(self) -> List[SettingsError]:
        # Returns and clears accumulated errors (load failures, write failures, etc.)
        drained = self.errors.copy()
        self.errors.clear()
        return drained

    async def reload(self):
        # Flushes pending writes, reloads both scopes from disk, merges them
        # Clears modification tracking so next change is detected fresh
        await self.flush()
        global_load = SettingsManager._try_load_from_storage(self.storage, "global")
        if not global_load[1]:
            self.global_settings = global_load[0]
            self.global_settings_load_error = None
        else:
            self.global_settings_load_error = global_load[1]
            self._record_error("global", global_load[1])

        # Reset tracking after reload so we see only new changes from here forward
        self.modified_fields.clear()
        self.modified_nested_fields.clear()
        self.modified_project_fields.clear()
        self.modified_project_nested_fields.clear()

        project_load = SettingsManager._try_load_from_storage(self.storage, "project")
        if not project_load[1]:
            self.project_settings = project_load[0]
            self.project_settings_load_error = None
        else:
            self.project_settings_load_error = project_load[1]
            self._record_error("project", project_load[1])

        self.settings = self._deep_merge_settings(self.global_settings, self.project_settings)

    def apply_overrides(self, overrides: Dict[str, Any]):
        # Temporary runtime overrides (not persisted); typically used for CLI flags
        override_settings = Settings(**overrides)
        self.settings = self._deep_merge_settings(self.settings, override_settings)  

    # =========================================================================  
    # Getter/Setter Methods  
    # =========================================================================  

    def get_default_provider(self) -> Optional[str]:    
        return self.settings.default_provider    
        
    def set_default_provider(self, provider: str):    
        self.global_settings.default_provider = provider    
        self._mark_modified("default_provider")    
        self._save()    
        
    def get_default_model(self) -> Optional[str]:    
        return self.settings.default_model    
        
    def set_default_model(self, model: str):    
        self.global_settings.default_model = model    
        self._mark_modified("default_model")    
        self._save()  
  
    def set_default_model_and_provider(self, provider: str, model_id: str):  
        self.global_settings.default_provider = provider  
        self.global_settings.default_model = model_id  
        self._mark_modified("default_provider")  
        self._mark_modified("default_model")  
        self._save()  
        
    def get_theme(self) -> Optional[str]:    
        return self.settings.theme    
        
    def set_theme(self, theme: str):    
        self.global_settings.theme = theme    
        self._mark_modified("theme")    
        self._save()    
        
    def get_default_thinking_level(self) -> Optional[str]:    
        return self.settings.default_thinking_level    
        
    def set_default_thinking_level(self, level: str):    
        self.global_settings.default_thinking_level = level    
        self._mark_modified("default_thinking_level")    
        self._save()    
        
    def get_transport(self) -> str:    
        return self.settings.transport or "auto"    
        
    def set_transport(self, transport: str):    
        self.global_settings.transport = transport    
        self._mark_modified("transport")    
        self._save()    
        
    def get_compaction_enabled(self) -> bool:    
        return self.settings.compaction.get("enabled") if self.settings.compaction else True    
        
    def set_compaction_enabled(self, enabled: bool):    
        if not self.global_settings.compaction:    
            self.global_settings.compaction = {}    
        self.global_settings.compaction["enabled"] = enabled    
        self._mark_modified("compaction", "enabled")    
        self._save()    
        
    def get_packages(self) -> list:    
        return self.settings.packages or []    
        
    def set_packages(self, packages: list):    
        self.global_settings.packages = packages    
        self._mark_modified("packages")    
        self._save()    
        
    def get_extension_paths(self) -> list[str]:    
        return self.settings.extensions or []    
        
    def set_extension_paths(self, paths: list[str]):    
        self.global_settings.extensions = paths    
        self._mark_modified("extensions")    
        self._save()    
        
    def get_skill_paths(self) -> list[str]:    
        return self.settings.skills or []    
        
    def set_skill_paths(self, paths: list[str]):    
        self.global_settings.skills = paths    
        self._mark_modified("skills")    
        self._save()  
  
    def get_steering_mode(self) -> str:  
        return self.settings.steering_mode or "one-at-a-time"  
  
    def set_steering_mode(self, mode: str):  
        self.global_settings.steering_mode = mode  
        self._mark_modified("steering_mode")  
        self._save()  
  
    def get_follow_up_mode(self) -> str:  
        return self.settings.follow_up_mode or "one-at-a-time"  
  
    def set_follow_up_mode(self, mode: str):  
        self.global_settings.follow_up_mode = mode  
        self._mark_modified("follow_up_mode")  
        self._save()  
  
    def get_show_images(self) -> bool:  
        return self.settings.terminal.get("show_images") if self.settings.terminal else True  
  
    def set_show_images(self, show: bool):  
        if not self.global_settings.terminal:  
            self.global_settings.terminal = {}  
        self.global_settings.terminal["show_images"] = show  
        self._mark_modified("terminal", "show_images")  
        self._save()  
  
    def get_image_width_cells(self) -> int:  
        width = self.settings.terminal.get("image_width_cells") if self.settings.terminal else None  
        if not isinstance(width, (int, float)) or not width:  
            return 60  
        return max(1, int(width))  
  
    def set_image_width_cells(self, width: int):  
        if not self.global_settings.terminal:  
            self.global_settings.terminal = {}  
        self.global_settings.terminal["image_width_cells"] = max(1, int(width))  
        self._mark_modified("terminal", "image_width_cells")  
        self._save()  
  
    def get_clear_on_shrink(self) -> bool:  
        if self.settings.terminal and self.settings.terminal.get("clear_on_shrink") is not None:  
            return self.settings.terminal["clear_on_shrink"]  
        return False  
  
    def set_clear_on_shrink(self, enabled: bool):  
        if not self.global_settings.terminal:  
            self.global_settings.terminal = {}  
        self.global_settings.terminal["clear_on_shrink"] = enabled  
        self._mark_modified("terminal", "clear_on_shrink")  
        self._save()  
  
    def get_show_terminal_progress(self) -> bool:  
        return self.settings.terminal.get("show_terminal_progress") if self.settings.terminal else False  
  
    def set_show_terminal_progress(self, enabled: bool):  
        if not self.global_settings.terminal:  
            self.global_settings.terminal = {}  
        self.global_settings.terminal["show_terminal_progress"] = enabled  
        self._mark_modified("terminal", "show_terminal_progress")  
        self._save()  
  
    def get_image_auto_resize(self) -> bool:  
        return self.settings.images.get("auto_resize") if self.settings.images else True  
  
    def set_image_auto_resize(self, enabled: bool):  
        if not self.global_settings.images:  
            self.global_settings.images = {}  
        self.global_settings.images["auto_resize"] = enabled  
        self._mark_modified("images", "auto_resize")  
        self._save()  
  
    def get_block_images(self) -> bool:
        return self.settings.images.get("block_images") if self.settings.images else False

    def set_block_images(self, blocked: bool):
        if not self.global_settings.images:
            self.global_settings.images = {}
        self.global_settings.images["block_images"] = blocked
        self._mark_modified("images", "block_images")
        self._save()  
  
    def get_enable_skill_commands(self) -> bool:  
        return self.settings.enable_skill_commands if self.settings.enable_skill_commands is not None else True  
  
    def set_enable_skill_commands(self, enabled: bool):  
        self.global_settings.enable_skill_commands = enabled  
        self._mark_modified("enable_skill_commands")  
        self._save()  
  
    def get_tree_filter_mode(self) -> str:  
        mode = self.settings.tree_filter_mode  
        valid = ["default", "no-tools", "user-only", "labeled-only", "all"]  
        return mode if mode in valid else "default"  
  
    def set_tree_filter_mode(self, mode: str):  
        self.global_settings.tree_filter_mode = mode  
        self._mark_modified("tree_filter_mode")  
        self._save()  
  
    def get_show_hardware_cursor(self) -> bool:  
        return self.settings.show_hardware_cursor if self.settings.show_hardware_cursor is not None else False  
  
    def set_show_hardware_cursor(self, enabled: bool):  
        self.global_settings.show_hardware_cursor = enabled  
        self._mark_modified("show_hardware_cursor")  
        self._save()  
  
    def get_editor_padding_x(self) -> int:  
        return self.settings.editor_padding_x if self.settings.editor_padding_x is not None else 0  
  
    def set_editor_padding_x(self, padding: int):  
        self.global_settings.editor_padding_x = max(0, min(3, int(padding)))  
        self._mark_modified("editor_padding_x")  
        self._save()  
  
    def get_autocomplete_max_visible(self) -> int:  
        return self.settings.autocomplete_max_visible if self.settings.autocomplete_max_visible is not None else 5  
  
    def set_autocomplete_max_visible(self, max_visible: int):  
        self.global_settings.autocomplete_max_visible = max(3, min(20, int(max_visible)))  
        self._mark_modified("autocomplete_max_visible")  
        self._save()  
  
    def get_code_block_indent(self) -> str:  
        return self.settings.markdown.get("code_block_indent") if self.settings.markdown else "  "

    def get_session_dir(self)->Path:
        if self.settings.session_dir is None:
            return None
        if self.settings.session_dir.startswith("~"):
            return Path.joinpath(Path.home(),self.settings.session_dir[1:]).resolve()
        return Path(self.settings.session_dir).resolve()
        

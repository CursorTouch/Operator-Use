from program.settings.storage import SettingsStorage, FileSettingsStorage, InMemorySettingsStorage, LockResult
from program.settings.types import Settings,SCOPE
from typing import Optional, Set
from pathlib import Path
import copy
import json

class SettingsManager:
    def __init__(self, storage: SettingsStorage, initial_global: Settings, initial_project: Settings):
        self.storage = storage
        self.global_settings = initial_global  
        self.project_settings = initial_project  
        self.settings = self._deep_merge_settings(initial_global, initial_project)  
        self.modified_fields: Set[str] = set()  
        self.modified_nested_fields: dict[str, Set[str]] = {}  
        self.modified_project_fields: Set[str] = set()  
        self.modified_project_nested_fields: dict[str, Set[str]] = {}  
      
    @staticmethod  
    def create(cwd: Path) -> SettingsManager:  
        storage = FileSettingsStorage(cwd)  
        return SettingsManager.from_storage(storage)  
      
    @staticmethod  
    def from_storage(storage: SettingsStorage) -> SettingsManager:  
        global_load = SettingsManager._try_load_from_storage(storage, "global")  
        project_load = SettingsManager._try_load_from_storage(storage, "project")  
        return SettingsManager(storage, global_load, project_load)  
      
    @staticmethod  
    def in_memory(settings: dict = {}) -> SettingsManager:  
        storage = InMemorySettingsStorage()  
        storage.with_lock("global", lambda _: LockResult(result=None, next=json.dumps(settings, indent=2)))  
        return SettingsManager.from_storage(storage)  
      
    @staticmethod  
    def _load_from_storage(storage: SettingsStorage, scope: SCOPE) -> Settings:  
        def load_fn(current):  
            if not current:  
                return LockResult(result=Settings(), next=None)  
            data = json.loads(current)  
            return LockResult(result=Settings(**data), next=None)  
          
        return storage.with_lock(scope, load_fn).result  
      
    @staticmethod  
    def _try_load_from_storage(storage: SettingsStorage, scope: str) -> Settings:  
        try:  
            return SettingsManager._load_from_storage(storage, scope)  
        except Exception:  
            return Settings()  
      
    def _deep_merge_settings(self, global_settings: Settings, project_settings: Settings) -> Settings:  
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
        self.modified_fields.add(field)  
        if nested_field:  
            if field not in self.modified_nested_fields:  
                self.modified_nested_fields[field] = set()  
            self.modified_nested_fields[field].add(nested_field)  
      
    def _mark_project_modified(self, field: str, nested_field: Optional[str] = None):  
        self.modified_project_fields.add(field)  
        if nested_field:  
            if field not in self.modified_project_nested_fields:  
                self.modified_project_nested_fields[field] = set()  
            self.modified_project_nested_fields[field].add(nested_field)  
      
    def _persist_scoped_settings(self, scope: str, snapshot_settings: Settings,   
                                  modified_fields: Set[str],   
                                  modified_nested_fields: dict[str, Set[str]]):  
        def persist_fn(current):  
            current_file_settings = Settings()  
            if current:  
                current_file_settings = Settings(**json.loads(current))  
              
            merged_settings = copy.deepcopy(current_file_settings)  
              
            for field in modified_fields:  
                value = getattr(snapshot_settings, field)  
                if field in modified_nested_fields and isinstance(value, dict):  
                    base_nested = getattr(current_file_settings, field) or {}  
                    in_memory_nested = value  
                    merged_nested = {**base_nested}  
                    for nested_key in modified_nested_fields[field]:  
                        merged_nested[nested_key] = in_memory_nested.get(nested_key)  
                    setattr(merged_settings, field, merged_nested)  
                else:  
                    setattr(merged_settings, field, value)  
              
            return LockResult(result=None, next=json.dumps(merged_settings.__dict__, indent=2, default=str))  
          
        self.storage.with_lock(scope, persist_fn)  
      
    def _save(self):  
        self.settings = self._deep_merge_settings(self.global_settings, self.project_settings)  
        snapshot_global = copy.deepcopy(self.global_settings)  
        modified_fields = set(self.modified_fields)  
        modified_nested_fields = {k: set(v) for k, v in self.modified_nested_fields.items()}  
          
        self._persist_scoped_settings("global", snapshot_global, modified_fields, modified_nested_fields)  
      
    def _save_project_settings(self, settings: Settings):  
        self.project_settings = copy.deepcopy(settings)  
        self.settings = self._deep_merge_settings(self.global_settings, self.project_settings)  
          
        snapshot_project = copy.deepcopy(self.project_settings)  
        modified_fields = set(self.modified_project_fields)  
        modified_nested_fields = {k: set(v) for k, v in self.modified_project_nested_fields.items()}  
          
        self._persist_scoped_settings("project", snapshot_project, modified_fields, modified_nested_fields)  
      
    # Getter/Setter methods  
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
      
    def get_global_settings(self) -> Settings:  
        return copy.deepcopy(self.global_settings)  
      
    def get_project_settings(self) -> Settings:  
        return copy.deepcopy(self.project_settings)
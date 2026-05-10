from __future__ import annotations   
from program.session.storage import SettingsStorage, FileSettingsStorage, InMemorySettingsStorage, LockResult
from pathlib import Path  
import json  

class SettingsStore:  
    """Settings manager with storage abstraction."""  
      
    def __init__(self, storage: SettingsStorage):  
        self.storage = storage  
        self.global_settings: dict = self._load_scope("global")  
        self.project_settings: dict = self._load_scope("project")  
        self.settings = self._deep_merge(self.global_settings, self.project_settings)  
        self.modified_fields: set[str] = set()  
        self.modified_project_fields: set[str] = set()  
      
    def _load_scope(self, scope: str) -> dict:  
        result = self.storage.with_lock(scope, lambda current: LockResult(result=current, next=None))  
        return json.loads(result.result) if result.result else {}  
      
    def _deep_merge(self, base: dict, override: dict) -> dict:  
        result = base.copy()  
        for key, value in override.items():  
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):  
                result[key] = self._deep_merge(result[key], value)  
            else:  
                result[key] = value  
        return result  
      
    def _persist(self, scope: str):  
        settings_to_save = self.global_settings if scope == "global" else self.project_settings  
        modified_fields = self.modified_fields if scope == "global" else self.modified_project_fields  
          
        def persist_fn(current: str | None) -> LockResult:  
            current_data = json.loads(current) if current else {}  
            merged = current_data.copy()  
            for field in modified_fields:  
                merged[field] = settings_to_save[field]  
            return LockResult(result=None, next=json.dumps(merged, indent=2))  
          
        self.storage.with_lock(scope, persist_fn)  
        if scope == "global":  
            self.modified_fields.clear()  
        else:  
            self.modified_project_fields.clear()  
      
    @staticmethod  
    def create(cwd: Path, agent_dir: Path) -> SettingsStore:  
        storage = FileSettingsStorage(cwd, agent_dir)  
        return SettingsStore(storage)  
      
    @staticmethod  
    def from_storage(storage: SettingsStorage) -> SettingsStore:  
        return SettingsStore(storage)  
      
    @staticmethod  
    def in_memory(initial: dict | None = None) -> SettingsStore:  
        storage = InMemorySettingsStorage()  
        if initial:  
            storage.global_data = json.dumps(initial, indent=2)  
        return SettingsStore.from_storage(storage)  
      
    def get(self, key: str, default=None):  
        return self.settings.get(key, default)  
      
    def set(self, key: str, value, scope: str = "global"):  
        if scope == "global":  
            self.global_settings[key] = value  
            self.modified_fields.add(key)  
        else:  
            self.project_settings[key] = value  
            self.modified_project_fields.add(key)  
        self.settings = self._deep_merge(self.global_settings, self.project_settings)  
        self._persist(scope)  
      
    def get_global_settings(self) -> dict:  
        return self.global_settings.copy()  
      
    def get_project_settings(self) -> dict:  
        return self.project_settings.copy()
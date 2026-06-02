from __future__ import annotations  
from operator_use.settings.paths import get_settings_path
from operator_use.settings.types import LockResult, SCOPE
from abc import ABC, abstractmethod 
from typing import Callable
from filelock import FileLock  
from pathlib import Path  
  

class SettingsStorage(ABC):  
    """Abstract storage backend for settings."""  
      
    @abstractmethod  
    def with_lock(self, scope: SCOPE, fn: Callable[[str | None], LockResult]) -> LockResult:  
        """Execute fn with locked access to the storage."""  
        pass  
  
class FileSettingsStorage(SettingsStorage):
    """File-based storage backend with locking."""

    def __init__(self, cwd: Path, config_dir: Path | None = None):
        """Resolve global and project settings paths and ensure the global parent directory exists."""
        self.global_settings_path = (
            config_dir / "settings.json" if config_dir else get_settings_path()
        )
        self.project_settings_path = cwd / ".operator" / "settings.json"
        self._ensure_parent_dir(self.global_settings_path)

    def _ensure_parent_dir(self, path: Path) -> None:
        """Create the parent directory with restricted permissions (0o700) if absent."""
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _ensure_file_exists(self, path: Path) -> None:
        """Create an empty JSON object file with restricted permissions (0o600) if absent."""
        if not path.exists():
            path.write_text("{}", encoding="utf-8")
            path.chmod(0o600)

    def with_lock(self, scope: SCOPE, fn: Callable[[str | None], LockResult]) -> LockResult:
        """Acquire a file lock for the scope, pass current content to fn, and write fn's result."""
        path = self.global_settings_path if scope == SCOPE.GLOBAL else self.project_settings_path

        # Never auto-create the project-level .operator/ directory.
        # FileLock creates parent dirs automatically; guard against that by
        # checking upfront.  If the directory doesn't already exist, treat
        # project settings as empty and silently drop any writes.
        if scope == SCOPE.PROJECT and not path.parent.exists():
            result = fn(None)
            return LockResult(result=result.result, next=None)

        lock_path = path.with_suffix(".lock")

        with FileLock(lock_path):
            self._ensure_file_exists(path)
            current = path.read_text(encoding="utf-8") if path.exists() else "{}"
            result = fn(current)
            if result.next is not None:
                path.write_text(result.next, encoding="utf-8")
            return result
  
class InMemorySettingsStorage(SettingsStorage):
    """In-memory storage backend for testing."""

    def __init__(self):
        """Initialise with empty JSON objects for both scopes."""
        self.global_data: str = "{}"
        self.project_data: str = "{}"

    def with_lock(self, scope: SCOPE, fn: Callable[[str | None], LockResult]) -> LockResult:
        """Pass current in-memory content to fn and update the store if fn returns new content."""
        current = self.global_data if scope == SCOPE.GLOBAL else self.project_data
        result = fn(current)
        if result.next is not None:
            if scope == SCOPE.GLOBAL:
                self.global_data = result.next
            else:
                self.project_data = result.next
        return result
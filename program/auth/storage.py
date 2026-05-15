from abc import ABC, abstractmethod
from program.auth.types import LockResult
from filelock import FileLock
from typing import Callable, Awaitable
from pathlib import Path


class AuthStorage(ABC):
    """Abstract storage backend for auth credentials."""

    @abstractmethod
    def with_lock(self, fn: Callable[[str | None], LockResult]) -> LockResult:
        """Execute fn with exclusive access to storage."""
        pass

    @abstractmethod
    async def with_lock_async(self, fn: Callable[[str | None], Awaitable[LockResult]]) -> LockResult:
        """Execute async fn with exclusive access to storage."""
        pass


class FileAuthStorage(AuthStorage):
    """File-based storage backend with locking."""

    def __init__(self, store_path: Path):
        self.store_path = store_path
        self.lock_path = store_path.with_suffix(".lock")
        self._ensure_parent_dir()
        self._ensure_file_exists()

    def _ensure_parent_dir(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _ensure_file_exists(self) -> None:
        if not self.store_path.exists():
            self.store_path.write_text("{}", encoding="utf-8")
            self.store_path.chmod(0o600)

    def with_lock(self, fn: Callable[[str | None], LockResult]) -> LockResult:
        with FileLock(self.lock_path):
            current = self.store_path.read_text(encoding="utf-8") if self.store_path.exists() else None
            result = fn(current)
            if result.next is not None:
                self.store_path.write_text(result.next, encoding="utf-8")
                self.store_path.chmod(0o600)
            return result

    async def with_lock_async(self, fn: Callable[[str | None], Awaitable[LockResult]]) -> LockResult:
        with FileLock(self.lock_path):
            current = self.store_path.read_text(encoding="utf-8") if self.store_path.exists() else None
            result = await fn(current)
            if result.next is not None:
                self.store_path.write_text(result.next, encoding="utf-8")
                self.store_path.chmod(0o600)
            return result


class InMemoryAuthStorage(AuthStorage):
    """In-memory storage backend for testing."""

    def __init__(self):
        self._value: str | None = None

    def with_lock(self, fn: Callable[[str | None], LockResult]) -> LockResult:
        result = fn(self._value)
        if result.next is not None:
            self._value = result.next
        return result

    async def with_lock_async(self, fn: Callable[[str | None], Awaitable[LockResult]]) -> LockResult:
        result = await fn(self._value)
        if result.next is not None:
            self._value = result.next
        return result
  
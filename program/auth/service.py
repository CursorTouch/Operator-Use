import json  
from abc import ABC, abstractmethod  
from dataclasses import dataclass  
from pathlib import Path  
from typing import Callable, Generic, TypeVar  
from filelock import FileLock  
  
from program.llm.provider.registry import ProviderRegistry  
from program.llm.provider.oauth import OAuthLoginCallbacks  
from program.settings.paths import get_auth_path  
from program.auth.types import AuthCredential, OAuthCredential, APICredential, AuthType
  
T = TypeVar('T')  
  
@dataclass  
class LockResult:  
    result: T  
    next: str | None = None  
  
class AuthStorage(ABC):  
    """Abstract storage backend for auth credentials."""  
      
    @abstractmethod  
    def with_lock(self, fn: Callable[[str | None], LockResult]) -> LockResult:  
        """Execute fn with locked access to the storage (async)."""  
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
  
class InMemoryAuthStorage(AuthStorage):  
    """In-memory storage backend for testing."""  
      
    def __init__(self):  
        self._value: str | None = None  
      
    def with_lock(self, fn: Callable[[str | None], LockResult]) -> LockResult:  
        result = fn(self._value)  
        if result.next is not None:  
            self._value = result.next  
        return result  
  
class AuthStore:  
    """Credential storage with pluggable backends."""  
      
    def __init__(self, registry: ProviderRegistry, storage: AuthStorage):  
        self.registry = registry  
        self.storage = storage  
        self.data: dict[str, AuthCredential] = self._load()  
        self.runtime_overrides: dict[str, str] = {}  
      
    @staticmethod  
    def create(registry: ProviderRegistry, auth_path: Path | None = None) -> "AuthStore":  
        """Create AuthStore with file storage."""  
        path = auth_path or get_auth_path()  
        storage = FileAuthStorage(path)  
        return AuthStore(registry, storage)  
      
    @staticmethod  
    def from_storage(registry: ProviderRegistry, storage: AuthStorage) -> "AuthStore":  
        """Create AuthStore with custom storage."""  
        return AuthStore(registry, storage)  
      
    @staticmethod  
    def in_memory(registry: ProviderRegistry, initial: dict = {}) -> "AuthStore":  
        """Create AuthStore with in-memory storage for testing."""  
        storage = InMemoryAuthStorage()  
        storage.with_lock(lambda _: LockResult(result=None, next=json.dumps(initial, indent=2)))  
        return AuthStore.from_storage(registry, storage)  
      
    def _parse_storage_data(self, content: str | None) -> dict[str, AuthCredential]:  
        if not content:  
            return {}  
        raw_data = json.loads(content)  
        data: dict[str, AuthCredential] = {}  
        for k, v in raw_data.items():
            cred_type=v.get("type")
            match cred_type:
                case AuthType.OAuth:
                    data[k] = OAuthCredential(  
                        access=v.get("access", ""),  
                        refresh=v.get("refresh", ""),  
                        expires=v.get("expires", 0),  
                        account_id=v.get("account_id")  
                    )
                case AuthType.ApiKey:
                    data[k] = APICredential(
                        key=v.get("key", "")
                    )
        return data  
      
    def _load(self) -> dict[str, AuthCredential]:  
        try:  
            result = self.storage.with_lock(lambda current: LockResult(result=current))  
            return self._parse_storage_data(result.result)  
        except (FileNotFoundError, json.JSONDecodeError):  
            return {}  
      
    def _persist_provider_change(self, provider: str, credential: AuthCredential | None) -> None:  
        def update_fn(current: str | None) -> LockResult:  
            current_data = self._parse_storage_data(current)  
            merged = {**current_data}  
            if credential:  
                merged[provider] = credential  
            else:  
                merged.pop(provider, None)  
            return LockResult(result=None, next=json.dumps(merged, indent=2))  
          
        try:  
            self.storage.with_lock(update_fn)  
        except Exception:  
            pass  
      
    def get(self, provider: str) -> AuthCredential | None:  
        return self.data.get(provider)  
      
    def set(self, provider: str, credential: AuthCredential) -> None:  
        self.data[provider] = credential  
        self._persist_provider_change(provider=provider, credential=credential)  
      
    def remove(self, provider: str) -> None:  
        self.data.pop(provider, None)  
        self._persist_provider_change(provider=provider, credential=None)  
      
    def set_runtime_api_key(self, provider: str, api_key: str) -> None:  
        """Set a runtime API key override (not persisted)."""  
        self.runtime_overrides[provider] = api_key  
      
    def remove_runtime_api_key(self, provider: str) -> None:  
        """Remove a runtime API key override."""  
        self.runtime_overrides.pop(provider, None)  
      
    async def get_api_key(self, provider: str) -> str | None:  
        # Check runtime override first  
        if provider in self.runtime_overrides:  
            return self.runtime_overrides[provider]  
          
        credential = self.get(provider)  
        if credential is None:  
            return None  
          
        if isinstance(credential, APICredential):  
            return credential.key  
          
        if isinstance(credential, OAuthCredential):  
            oauth_provider = self.registry.get_oauth_provider(provider=provider)  
            if not oauth_provider:  
                return None  
              
            if oauth_provider.is_expired(credential=credential):  
                refreshed_credential = await self._refresh_oauth_token_with_lock(provider=provider)  
                if refreshed_credential:  
                    credential = refreshed_credential  
                else:  
                    return None  
            return oauth_provider.get_api_key(credential=credential)  
          
        return None  
      
    async def _refresh_oauth_token_with_lock(self, provider: str) -> OAuthCredential | None:  
        """Refresh OAuth token with file locking to prevent race conditions."""  
        oauth_provider = self.registry.get_oauth_provider(provider=provider)  
        if not oauth_provider:  
            return None  
          
        async def refresh_fn(current: str | None) -> LockResult:  
            current_data = self._parse_storage_data(current)  
            credential = current_data.get(provider=provider)  
              
            if not isinstance(credential, OAuthCredential):  
                return LockResult(result=None)  
              
            # Check if another instance already refreshed  
            if not oauth_provider.is_expired(credential=credential):  
                return LockResult(result=credential)  
              
            # Perform refresh  
            try:  
                refreshed_credential = await oauth_provider.refresh_token(credential=credential)  
                current_data[provider] = refreshed_credential  
                self.data = current_data  
                return LockResult(result=refreshed_credential, next=json.dumps(current_data, indent=2))  
            except Exception:  
                return LockResult(result=None)  
          
        result = self.storage.with_lock(refresh_fn)  
        return result.result  
      
    async def login(self, provider: str, callbacks: OAuthLoginCallbacks):  
        if oauth_provider := self.registry.get_oauth_provider(provider):  
            credential = await oauth_provider.login(callbacks=callbacks)  
            self.data[provider] = credential  
            self._persist_provider_change(provider, credential)  
      
    async def logout(self, provider: str):  
        if oauth_provider := self.registry.get_oauth_provider(provider):  
            if credential := self.get(provider):  
                if isinstance(credential, OAuthCredential):  
                    await oauth_provider.logout(credential=credential)  
            self.remove(provider)
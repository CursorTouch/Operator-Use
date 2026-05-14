from __future__ import annotations
from program.llm.provider.registry import ProviderRegistry  
from program.llm.provider.oauth import OAuthLoginCallbacks  
from program.settings.paths import get_auth_path  
from program.auth.types import AuthCredential, OAuthCredential, APICredential, AuthType, LockResult
from program.auth.storage import AuthStorage, FileAuthStorage, InMemoryAuthStorage
from pathlib import Path 
import json 

class AuthManager:
    """Credential storage with pluggable backends."""  
      
    def __init__(self, registry: ProviderRegistry, storage: AuthStorage):  
        self.registry = registry  
        self.storage = storage  
        self.data: dict[str, AuthCredential] = self._load()  
        self.runtime_overrides: dict[str, str] = {}  
      
    @staticmethod
    def create(registry: ProviderRegistry, auth_path: Path | None = None) -> "AuthManager":
        """Create AuthManager with file storage."""
        path = auth_path or get_auth_path()
        storage = FileAuthStorage(path)
        return AuthManager(registry, storage)  
      
    @staticmethod
    def from_storage(registry: ProviderRegistry, storage: AuthStorage) -> "AuthManager":
        """Create AuthManager with custom storage."""
        return AuthManager(registry, storage)  
      
    @staticmethod
    def in_memory(registry: ProviderRegistry, initial: dict = {}) -> "AuthManager":
        """Create AuthManager with in-memory storage for testing."""
        storage = InMemoryAuthStorage()
        storage.with_lock(lambda _: LockResult(result=None, next=json.dumps(initial, indent=2)))
        return AuthManager.from_storage(registry, storage)  
      
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
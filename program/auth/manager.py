from __future__ import annotations
import os
import json
from pathlib import Path

from program.llm.provider.registry import ProviderRegistry
from program.llm.provider.oauth import OAuthLoginCallbacks
from program.settings.paths import get_auth_path
from program.auth.types import AuthCredential, AuthStatus, OAuthCredential, APICredential, AuthType, LockResult
from program.auth.storage import AuthStorage, FileAuthStorage, InMemoryAuthStorage


def _get_env_api_key(provider: str) -> str | None:
    return os.environ.get(f"{provider.upper()}_API_KEY")


class AuthManager:
    """Credential storage with pluggable backends."""

    def __init__(self, registry: ProviderRegistry, storage: AuthStorage):
        self.registry = registry
        self.storage = storage
        self.runtime_overrides: dict[str, str] = {}
        self._load_error: Exception | None = None
        self._errors: list[Exception] = []
        self.data: dict[str, AuthCredential] = self._load()

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

    def _record_error(self, error: Exception) -> None:
        self._errors.append(error)

    def _parse_storage_data(self, content: str | None) -> dict[str, AuthCredential]:
        if not content:
            return {}
        raw_data = json.loads(content)
        data: dict[str, AuthCredential] = {}
        for k, v in raw_data.items():
            cred_type = v.get("type")
            match cred_type:
                case AuthType.OAuth:
                    data[k] = OAuthCredential(
                        access=v.get("access", ""),
                        refresh=v.get("refresh", ""),
                        expires=v.get("expires", 0),
                        account_id=v.get("account_id"),
                    )
                case AuthType.ApiKey:
                    data[k] = APICredential(key=v.get("key", ""))
        return data

    def _load(self) -> dict[str, AuthCredential]:
        try:
            result = self.storage.with_lock(lambda current: LockResult(result=current))
            self._load_error = None
            return self._parse_storage_data(result.result)
        except Exception as e:
            self._load_error = e
            self._record_error(e)
            return {}

    def _persist_provider_change(self, provider: str, credential: AuthCredential | None) -> None:
        if self._load_error:
            return

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
        except Exception as e:
            self._record_error(e)

    def reload(self) -> None:
        """Reload credentials from storage."""
        self.data = self._load()

    def get(self, provider: str) -> AuthCredential | None:
        return self.data.get(provider)

    def has(self, provider: str) -> bool:
        """Check if credentials exist for a provider in storage."""
        return provider in self.data

    def list(self) -> list[str]:
        """List all providers with stored credentials."""
        return list(self.data.keys())

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

    def get_auth_status(self, provider: str) -> AuthStatus:
        """Return auth status without exposing credential values."""
        if self.has(provider):
            return AuthStatus(configured=True, source="stored")
        if provider in self.runtime_overrides:
            return AuthStatus(configured=True, source="runtime", label="--api-key")
        env_key = f"{provider.upper()}_API_KEY"
        if os.environ.get(env_key):
            return AuthStatus(configured=True, source="env", label=env_key)
        return AuthStatus(configured=False)

    def drain_errors(self) -> list[Exception]:
        """Return and clear accumulated errors."""
        drained = list(self._errors)
        self._errors.clear()
        return drained

    async def get_api_key(self, provider: str) -> str | None:
        # 1. Runtime override
        if provider in self.runtime_overrides:
            return self.runtime_overrides[provider]

        credential = self.get(provider)

        match credential:
            case APICredential():
                return credential.key
            case OAuthCredential():
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

        # 2. Environment variable fallback
        return _get_env_api_key(provider)

    async def _refresh_oauth_token_with_lock(self, provider: str) -> OAuthCredential | None:
        """Refresh OAuth token with file locking to prevent race conditions."""
        oauth_provider = self.registry.get_oauth_provider(provider=provider)
        if not oauth_provider:
            return None

        async def refresh_fn(current: str | None) -> LockResult:
            current_data = self._parse_storage_data(current)
            credential = current_data.get(provider)

            if not isinstance(credential, OAuthCredential):
                return LockResult(result=None)

            # Check if another instance already refreshed
            if not oauth_provider.is_expired(credential=credential):
                return LockResult(result=credential)

            try:
                refreshed_credential = await oauth_provider.refresh_token(credential=credential)
                current_data[provider] = refreshed_credential
                self.data = current_data
                return LockResult(result=refreshed_credential, next=json.dumps(current_data, indent=2))
            except Exception as e:
                self._record_error(e)
                return LockResult(result=None)

        result = await self.storage.with_lock_async(refresh_fn)
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

from __future__ import annotations
import builtins
import os
import re
import json
import subprocess
from pathlib import Path

from operator_use.inference.provider.registry import TextProviderRegistry
from operator_use.inference.provider.oauth import OAuthLoginCallbacks
from operator_use.inference.provider.oauth.anthropic_claude_code import read_cc_keychain_credential
from operator_use.inference.provider.oauth.openai_codex import read_codex_file_credential
from operator_use.inference.provider.oauth.google_antigravity import read_antigravity_file_credential
from operator_use.settings.paths import get_providers_auth_path
from operator_use.auth.types import AuthCredential, AuthStatus, OAuthCredential, APICredential, AuthType, LockResult
from operator_use.auth.storage import AuthStorage, FileAuthStorage, InMemoryAuthStorage

_NATIVE_READERS: dict[str, object] = {
    "anthropic-claude-code": read_cc_keychain_credential,
    "openai-codex": read_codex_file_credential,
    "google-antigravity": read_antigravity_file_credential,
}


_cmd_cache: dict[str, str] = {}

_VAR_RE = re.compile(r'\$(\$|!|\{([^}]+)\}|([A-Za-z_][A-Za-z0-9_]*))')


def _resolve_key(key: str) -> str | None:
    """Resolve an API key value supporting !cmd execution and $VAR interpolation."""
    if key.startswith("!"):
        cmd = key[1:]
        if cmd not in _cmd_cache:
            try:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
                _cmd_cache[cmd] = result.stdout.strip()
            except Exception:
                return None
        val = _cmd_cache[cmd]
        return val or None

    if "$" not in key:
        return key

    unresolved = False

    def _replace(m: re.Match) -> str:
        nonlocal unresolved
        if m.group(1) == "$":
            return "$"
        if m.group(1) == "!":
            return "!"
        var_name = m.group(2) or m.group(3)
        val = os.environ.get(var_name)
        if val is None:
            unresolved = True
            return ""
        return val

    resolved = _VAR_RE.sub(_replace, key)
    return None if unresolved else resolved


def _env_api_key_names(provider: str) -> list[str]:
    canonical = f"{provider.upper()}_API_KEY"
    candidates = [canonical]
    normalized = f"{provider.upper().replace('-', '_')}_API_KEY"
    if normalized != canonical:
        candidates.append(normalized)
    if provider == "kilocode":
        candidates.append("KILO_API_KEY")
    return candidates


def _get_env_api_key_entry(provider: str) -> tuple[str, str] | None:
    for name in _env_api_key_names(provider):
        value = os.environ.get(name)
        if value:
            return name, value
    return None


def _get_env_api_key(provider: str) -> str | None:
    entry = _get_env_api_key_entry(provider)
    return entry[1] if entry else None


class ProviderAuthManager:
    """
    Manages LLM provider credentials (API keys and OAuth tokens).

    Credentials are stored in auth/providers.json keyed by provider name.
    Supports API keys, OAuth tokens with auto-refresh, runtime overrides,
    and environment variable fallback.
    """

    def __init__(self, registry: TextProviderRegistry, storage: AuthStorage):
        self.registry = registry
        self.storage = storage
        self.runtime_overrides: dict[str, str] = {}
        self._load_error: Exception | None = None
        self._errors: builtins.list[Exception] = []
        self.data: dict[str, AuthCredential] = self._load()

    @staticmethod
    def create(registry: TextProviderRegistry, auth_path: Path | None = None) -> ProviderAuthManager:
        path = auth_path or get_providers_auth_path()
        return ProviderAuthManager(registry, FileAuthStorage(path))

    @staticmethod
    def from_storage(registry: TextProviderRegistry, storage: AuthStorage) -> ProviderAuthManager:
        return ProviderAuthManager(registry, storage)

    @staticmethod
    def in_memory(registry: TextProviderRegistry, initial: dict = {}) -> ProviderAuthManager:
        storage = InMemoryAuthStorage()
        storage.with_lock(lambda _: LockResult(result=None, next=json.dumps(initial, indent=2)))
        return ProviderAuthManager.from_storage(registry, storage)

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
                    raw_extra = v.get("extra") or {}
                    extra = {str(ek): str(ev) for ek, ev in raw_extra.items()} if isinstance(raw_extra, dict) else {}
                    # Backwards-compat: fold legacy top-level account_id into extra.
                    legacy_account_id = v.get("account_id")
                    if legacy_account_id and "account_id" not in extra:
                        extra["account_id"] = str(legacy_account_id)
                    data[k] = OAuthCredential(
                        access=v.get("access", ""),
                        refresh=v.get("refresh", ""),
                        expires=v.get("expires", 0),
                        extra=extra,
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

    @staticmethod
    def _serialize_credential(credential: AuthCredential) -> dict:
        if isinstance(credential, OAuthCredential):
            return {
                "type": AuthType.OAuth,
                "access": credential.access,
                "refresh": credential.refresh,
                "expires": credential.expires,
                "extra": dict(credential.extra),
            }
        return {"type": AuthType.ApiKey, "key": credential.key}

    def _persist_provider_change(self, provider: str, credential: AuthCredential | None) -> None:
        if self._load_error:
            return

        def update_fn(current: str | None) -> LockResult:
            current_data = self._parse_storage_data(current)
            merged = {k: self._serialize_credential(v) for k, v in current_data.items()}
            if credential:
                merged[provider] = self._serialize_credential(credential)
            else:
                merged.pop(provider, None)
            return LockResult(result=None, next=json.dumps(merged, indent=2))

        try:
            self.storage.with_lock(update_fn)
        except Exception as e:
            self._record_error(e)

    def reload(self) -> None:
        self.data = self._load()

    def get(self, provider: str) -> AuthCredential | None:
        return self.data.get(provider)

    def has(self, provider: str) -> bool:
        return provider in self.data

    def list(self) -> builtins.list[str]:
        return builtins.list(self.data.keys())

    def set(self, provider: str, credential: AuthCredential) -> None:
        self.data[provider] = credential
        self._persist_provider_change(provider=provider, credential=credential)

    def remove(self, provider: str) -> None:
        self.data.pop(provider, None)
        self._persist_provider_change(provider=provider, credential=None)

    def set_runtime_api_key(self, provider: str, api_key: str) -> None:
        self.runtime_overrides[provider] = api_key

    def remove_runtime_api_key(self, provider: str) -> None:
        self.runtime_overrides.pop(provider, None)

    def get_auth_status(self, provider: str) -> AuthStatus:
        if self.has(provider):
            return AuthStatus(configured=True, source="stored")
        if provider in self.runtime_overrides:
            return AuthStatus(configured=True, source="runtime", label="--api-key")
        env_entry = _get_env_api_key_entry(provider)
        if env_entry:
            env_key, _ = env_entry
            return AuthStatus(configured=True, source="env", label=env_key)
        return AuthStatus(configured=False)

    def drain_errors(self) -> builtins.list[Exception]:
        drained = builtins.list(self._errors)
        self._errors.clear()
        return drained

    def _try_native_import(self, provider: str) -> OAuthCredential | None:
        reader = _NATIVE_READERS.get(provider)
        if reader is None:
            return None
        try:
            return reader()  # type: ignore[call-arg]
        except Exception:
            return None

    async def get_api_key(self, provider: str) -> str | None:
        if provider in self.runtime_overrides:
            return self.runtime_overrides[provider]

        credential: AuthCredential | None = self.get(provider)
        oauth_provider = self.registry.get_oauth_provider(provider=provider)

        # Fast path: providers.json has a valid non-expired credential.
        if credential is not None:
            match credential:
                case APICredential():
                    return _resolve_key(credential.key)
                case OAuthCredential() if oauth_provider and not oauth_provider.is_expired(credential):
                    return oauth_provider.get_api_key(credential)

        # providers.json is empty or expired — try native store as rescue.
        native = self._try_native_import(provider)
        if isinstance(native, OAuthCredential) and oauth_provider:
            if not oauth_provider.is_expired(native):
                # Native has a fresh token: write to providers.json and use it.
                self.set(provider, native)
                return oauth_provider.get_api_key(native)
            elif credential is None:
                # Nothing stored at all; seed providers.json with native so
                # the refresh cycle has a refresh token to work with.
                self.set(provider, native)
                credential = native

        # credential (from providers.json or seeded from native) may be
        # expired but holds a refresh token — try to refresh it.
        if isinstance(credential, OAuthCredential) and oauth_provider:
            refreshed = await self._refresh_oauth_token_with_lock(provider=provider)
            if refreshed:
                return oauth_provider.get_api_key(refreshed)
            return None

        return _get_env_api_key(provider)

    async def _refresh_oauth_token_with_lock(self, provider: str) -> OAuthCredential | None:
        oauth_provider = self.registry.get_oauth_provider(provider=provider)
        if not oauth_provider:
            return None

        async def refresh_fn(current: str | None) -> LockResult[OAuthCredential | None]:
            current_data = self._parse_storage_data(current)
            credential = current_data.get(provider)
            if not isinstance(credential, OAuthCredential):
                return LockResult[OAuthCredential | None](result=None)
            if not oauth_provider.is_expired(credential=credential):
                return LockResult[OAuthCredential | None](result=credential)
            try:
                refreshed = await oauth_provider.refresh_token(credential=credential)
                if credential.extra:
                    merged_extra = dict(credential.extra)
                    merged_extra.update(refreshed.extra)
                    refreshed.extra = merged_extra
                current_data[provider] = refreshed
                self.data = current_data
                serialized = {k: self._serialize_credential(v) for k, v in current_data.items()}
                return LockResult[OAuthCredential | None](result=refreshed, next=json.dumps(serialized, indent=2))
            except Exception as e:
                self._record_error(e)
                return LockResult[OAuthCredential | None](result=None)

        result = await self.storage.with_lock_async(refresh_fn)
        return result.result

    async def login(self, provider: str, callbacks: OAuthLoginCallbacks) -> None:
        if oauth_provider := self.registry.get_oauth_provider(provider):
            credential = await oauth_provider.login(callbacks=callbacks)
            self.data[provider] = credential
            self._persist_provider_change(provider, credential)

    async def logout(self, provider: str) -> None:
        if oauth_provider := self.registry.get_oauth_provider(provider):
            if credential := self.get(provider):
                if isinstance(credential, OAuthCredential):
                    await oauth_provider.logout(credential=credential)
        self.remove(provider)

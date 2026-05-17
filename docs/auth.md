# Auth

`AuthManager` stores and retrieves credentials for all LLM providers. It supports two credential types — OAuth tokens and plain API keys — and three credential sources: stored in `auth.json`, set at runtime, or read from environment variables.

## Credential types

```python
@dataclass
class OAuthCredential:
    access: str        # access token
    refresh: str       # refresh token
    expires: int       # Unix timestamp in milliseconds
    account_id: str | None = None

@dataclass
class APICredential:
    key: str           # plain API key
```

`AuthCredential = OAuthCredential | APICredential`

## Storage

Credentials are stored in `auth.json` in the platform config directory (`~/.config/program/auth.json` by default). The file is created with mode `0600` (owner read/write only) and the parent directory with mode `0700`.

The JSON format is a flat object keyed by provider ID:

```json
{
  "anthropic": {
    "type": "oauth",
    "access": "...",
    "refresh": "...",
    "expires": 1234567890000,
    "account_id": "user@example.com"
  },
  "openai": {
    "type": "api_key",
    "key": "sk-..."
  }
}
```

All reads and writes go through a `FileLock` to prevent race conditions between concurrent processes (e.g., multiple agent instances refreshing the same token simultaneously).

### Storage backends

| Backend | When to use |
|---|---|
| `FileAuthStorage` | Production — persists to disk with `FileLock` |
| `InMemoryAuthStorage` | Testing — no disk I/O, no locking |

`AuthManager.create(registry)` creates a `FileAuthStorage` at the default path. `AuthManager.in_memory(registry, initial)` creates an in-memory store pre-seeded with initial data.

## Credential resolution

`AuthManager.get_api_key(provider)` resolves a usable API key in priority order:

1. **Runtime override** — set via `set_runtime_api_key(provider, key)`. Takes priority over everything else. Not persisted.
2. **Stored API key** — `APICredential` from `auth.json`.
3. **Stored OAuth token** — `OAuthCredential` from `auth.json`. If expired, triggers a token refresh (with file locking to prevent duplicate refreshes). Derives a usable API key via `oauth_provider.get_api_key(credential)`.
4. **Environment variable** — `{PROVIDER_ID_UPPER}_API_KEY` (e.g., `ANTHROPIC_API_KEY`).

Returns `None` if no credential is found at any level.

## Auth status

`get_auth_status(provider)` returns an `AuthStatus` without exposing credential values:

```python
@dataclass
class AuthStatus:
    configured: bool
    source: Literal["stored", "runtime", "env"] | None = None
    label: str | None = None    # env var name for env-sourced keys
```

Used by `/auth` and `/login` to show which providers are configured.

## Token refresh

OAuth tokens expire. When `get_api_key()` detects an expired token it calls `_refresh_oauth_token_with_lock()`:

1. Acquire the file lock.
2. Re-read `auth.json` — another process may have already refreshed the token.
3. If the token is still expired, call `oauth_provider.refresh_token(credential)`.
4. Write the new credential back to `auth.json` atomically under the lock.
5. Update `self.data` in-memory.

If the refresh fails (network error, revoked token), `None` is returned and the caller must re-authenticate.

## Login and logout

```python
await auth_manager.login(provider_id, callbacks)
await auth_manager.logout(provider_id)
```

**login**: Delegates to `OAuthProvider.login(callbacks)`. The `callbacks` object receives the authorization URL and handles user interaction (browser open, device code prompt). After the exchange, the credential is stored in-memory and persisted.

**logout**: Calls `OAuthProvider.logout(credential)` to revoke the token server-side if supported. Then removes the credential from in-memory storage and persists the deletion.

## AuthManager is shared across LLM instances

`LLM._auth_store` is a class-level `AuthManager`. All `LLM` instances share it. A credential stored by `/login` is immediately visible to any LLM constructed afterward.

## OAuthLoginCallbacks

```python
class OAuthLoginCallbacks:
    on_url: Callable[[str], None]            # receive the auth URL
    on_device_code: Callable[[str], None]    # receive device code (if needed)
    on_complete: Callable[[], None]          # called after login completes
```

The `/login` command provides callbacks that open the browser (`webbrowser.open(url)`) and prompt for confirmation on the terminal.

## Environment variable fallback

Every provider supports a `{PROVIDER_ID_UPPER}_API_KEY` environment variable. This is checked last — after stored credentials and runtime overrides. It is never persisted.

Examples:
- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `MISTRAL_API_KEY`
- `GOOGLE_API_KEY`

## Error handling

Storage errors are non-fatal. `AuthManager._load()` catches exceptions from the file lock and records them. If the initial load fails, `self.data` is empty and `self._load_error` is set. Subsequent `_persist_provider_change()` calls are no-ops when `_load_error` is set, so the in-memory state is not silently lost to disk when the storage is broken.

`drain_errors()` returns and clears the accumulated error list, allowing the caller to surface any storage failures.

## Related documents

- [inference.md](./inference.md) — How `LLM` uses `AuthManager` at construction time
- [commands.md](./commands.md) — `/login`, `/logout`, `/auth` commands

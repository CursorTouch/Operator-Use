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

## CLI Commands (`operator auth`)

The `operator` command-line tool provides a unified `auth` CLI suite to manage provider credentials (both API keys and interactive OAuth logins).

### List all providers / status

```bash
operator auth
```
or
```bash
operator auth list
```
Displays a list of all registered providers (both OAuth and API-key), along with their current authentication status and configuration source.

To view detailed status for a single provider:
```bash
operator auth list <provider-id>
```
or
```bash
operator auth status <provider-id>
```

### Authenticate a provider

To set an API key for a key-based provider:
```bash
operator auth set <provider-id> --api-key <key>
```
Example:
```bash
operator auth set openai --api-key sk-...
```

To initiate the OAuth login flow for an OAuth provider:
```bash
operator auth set <provider-id> --oauth
```
Example:
```bash
operator auth set claude --oauth
```
This launches a browser window to authenticate with the provider and guides you through the process in the terminal.

### Revoke/remove credentials

To remove stored credentials (either deleting an API key or logging out of an OAuth session):
```bash
operator auth unset <provider-id>
```

## Error handling

Storage errors are non-fatal. `AuthManager._load()` catches exceptions from the file lock and records them. If the initial load fails, `self.data` is empty and `self._load_error` is set. Subsequent `_persist_provider_change()` calls are no-ops when `_load_error` is set, so the in-memory state is not silently lost to disk when the storage is broken.

`drain_errors()` returns and clears the accumulated error list, allowing the caller to surface any storage failures.

---

## Channel credentials (`ChannelAuthManager`)

Channel bot tokens are stored separately from LLM provider credentials. They are never mixed with `auth.json`.

**Storage:** `~/.program/auth/channels.json`, mode `0600`.

```json
{
  "telegram": { "bot_token": "..." },
  "discord":  { "bot_token": "..." },
  "slack":    { "bot_token": "xoxb-...", "app_token": "xapp-..." },
  "twitch":   { "token": "..." },
  "email":    { "username": "user@example.com", "password": "..." }
}
```

### Credential resolution order

For each field, `ChannelAuthManager` checks in order:

1. `channels.json` — stored value (if non-empty).
2. Environment variable — see table below.

Environment variables take effect when the JSON field is blank:

| Channel | Field | Env var |
|---|---|---|
| `telegram` | `bot_token` | `TELEGRAM_BOT_TOKEN` |
| `discord` | `bot_token` | `DISCORD_BOT_TOKEN` |
| `slack` | `bot_token` | `SLACK_BOT_TOKEN` |
| `slack` | `app_token` | `SLACK_APP_TOKEN` |
| `twitch` | `token` | `TWITCH_TOKEN` |
| `email` | `username` | `EMAIL_USERNAME` |
| `email` | `password` | `EMAIL_PASSWORD` |

### Usage

```python
from program.auth.channels import ChannelAuthManager
from program.settings.paths import get_channels_auth_path

auth = ChannelAuthManager(get_channels_auth_path())

# Read
token = auth.telegram.bot_token
bot_token, app_token = auth.slack.bot_token, auth.slack.app_token

# Write (persists immediately to channels.json)
auth.set_telegram(bot_token="...")
auth.set_slack(bot_token="xoxb-...", app_token="xapp-...")
auth.set_email(username="bot@example.com", password="...")
```

`ChannelAuthManager` is created by the Runtime alongside the `GatewayManager`. If `channels.json` cannot be parsed, it logs a warning and starts with empty credentials (channels that require tokens will log their own warning and be skipped at startup).

## Related documents

- [inference.md](./inference.md) — How `LLM` uses `AuthManager` at construction time
- [commands.md](./commands.md) — `/login`, `/logout`, `/auth` commands
- [gateway.md](./gateway.md) — How channel credentials are used at channel startup

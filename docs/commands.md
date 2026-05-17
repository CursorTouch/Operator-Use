# Commands

Slash commands are text inputs that start with `/`. They are dispatched by `CommandRegistry` instead of being forwarded to the Agent as user prompts. The Runtime checks `parse_command()` on every `user_input()` call and routes accordingly.

## Parsing

```python
def parse_command(text: str) -> CommandParseResult | None:
    stripped = text.strip()
    if not stripped.startswith('/'):
        return None
    parts = stripped[1:].split()
    return CommandParseResult(name=parts[0].lower(), args=parts[1:], raw=stripped)
```

`/compact shrink context` → `CommandParseResult(name="compact", args=["shrink", "context"], raw="/compact shrink context")`

Non-command text returns `None` and is forwarded to the Agent.

## CommandRegistry

`CommandRegistry` holds all registered commands and dispatches parsed input:

```python
registry.register(SlashCommandInfo(...))          # register one command
registry.register_from_extensions(ext_commands)   # bulk-register from extensions
registry.get("compact")                           # lookup by name or alias
registry.list()                                   # unique commands (aliases deduplicated)
await registry.dispatch(parsed)                   # run a command, returns True if found
```

Aliases are registered as additional keys pointing to the same `SlashCommandInfo`. `dispatch()` prints an "Unknown command" message and returns `False` for unrecognized commands.

Handlers are called with `(registry, args)`. If the handler returns a coroutine, it is awaited.

## Command discovery

`ResourceLoader` drives all command discovery. On `reload()` it loads commands from these directories in order (first-found wins on name collision):

| Directory | Purpose |
|---|---|
| `program/builtins/commands/` | Shipped built-in commands |
| `<project>/.program/agent/commands/` | Project-level custom commands |
| `~/.program/agent/commands/` | Global user commands |

A command file must export either `command = SlashCommandInfo(...)` or `commands = [SlashCommandInfo(...), ...]`.

```python
# .program/agent/commands/deploy.py
from program.commands.types import SlashCommandInfo

async def _handle_deploy(registry, args):
    ...

command = SlashCommandInfo(
    name='deploy',
    description='Deploy the current branch.',
    handler=_handle_deploy,
)
```

The `CommandRegistry` is created with the discovered commands passed as `discovered=`. Extension commands are then merged in via `register_from_extensions()` after that.

## SlashCommandInfo

```python
@dataclass
class SlashCommandInfo:
    name: str
    description: str
    handler: Callable[[CommandRegistry, list[str]], Awaitable[None] | None]
    aliases: list[str] = []
```

The `handler` receives the `CommandRegistry` (which carries a `registry.runtime` back-reference) and the parsed argument list.

## Built-in commands

| Command | Aliases | Description |
|---|---|---|
| `/login [provider]` | — | Log in to an OAuth provider interactively. Lists providers if no argument given. |
| `/logout [provider]` | — | Log out from an OAuth provider. Lists logged-in providers if no argument given. |
| `/auth` | — | Show authentication status for all providers (OAuth and API-key). |
| `/compact [instructions]` | — | Run compaction immediately. Optional custom instructions override the default summarization prompt. |
| `/new` | `/clear` | Start a new session (discards the current session history). |
| `/help` | `/?` | List all available commands with descriptions and aliases. |

### /login

Enumerates OAuth providers from `LLM._providers.get_oauth_providers()`. Shows a numbered list with current login status. Accepts a provider ID directly as an argument to skip the prompt.

The login flow calls `AuthManager.login(provider_id, callbacks)`. The `callbacks` object receives the authorization URL (opened in the browser), a device code prompt if needed, and a completion signal. After the OAuth exchange, credentials are persisted to `auth.json`.

### /logout

Lists currently logged-in OAuth providers. Calls `AuthManager.logout(provider_id)`, which revokes the token server-side (if the provider supports it) and removes the stored credential.

### /auth

Prints one line per provider — OAuth providers first, then API-key providers. Each line shows the provider name, status (`logged in` / `not logged in` / `api key set`), the source (`stored`, `runtime`, `env`), and the label for env-sourced keys (e.g., `ANTHROPIC_API_KEY`).

### /compact

Calls `runtime.current_session.run_compaction(custom_instructions)`. Requires the agent to be idle — raises immediately if a turn is in progress. Prints "Nothing to compact." if the session cannot be compacted (already at a compaction entry, or no valid cut point).

### /new (/clear)

Calls `runtime.new_session()`. This triggers `session_shutdown` and `session_start` events, creates a fresh JSONL file, and resets the Agent's state.

## Extension commands

Extensions register commands through the extension API:

```python
def setup(api):
    api.register_command(
        name="mycommand",
        description="Does something",
        handler=my_handler,
    )
```

At Runtime creation, `register_from_extensions()` wraps each extension command in a `SlashCommandInfo` and registers it alongside the built-ins. Extension command names must not collide with built-ins — if they do, the built-in takes precedence.

## Handler access to Runtime

Every command handler receives `registry: CommandRegistry`. The active `Runtime` is accessible via `registry.runtime`. From there:

- `registry.runtime.current_session` — the active `Agent`
- `registry.runtime.session_manager` — the active `SessionManager`
- All `Agent` methods: `run_compaction()`, `fork()`, `new_session()`, `abort()`, etc.

## Related documents

- [agent.md](./agent.md) — `run_compaction()`, `new_session()`, `fork()` called by commands
- [auth.md](./auth.md) — `AuthManager.login()` / `logout()` called by `/login` and `/logout`
- [extensions.md](./extensions.md) — Extension command registration

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
registry = CommandRegistry()                      # auto-loads builtins via from_builtins()
registry = CommandRegistry(discovered=cmds)       # use an explicit list (skips auto-load)
CommandRegistry.from_builtins()                   # returns the builtin SlashCommandInfo list
registry.register(SlashCommandInfo(...))          # register one command
registry.register_from_extensions(ext_commands)   # bulk-register from extensions
registry.get("compact")                           # lookup by name or alias
registry.list()                                   # unique commands (aliases deduplicated)
await registry.dispatch(parsed)                   # run a command, returns True if found
```

When `discovered` is omitted (or `None`), the constructor calls `from_builtins()` to seed the registry with the six built-in commands. Pass an explicit `discovered` list to override this — the `Runtime` always does so, passing the full list already loaded by `ResourceLoader` (which includes builtins plus any user-defined commands).

`from_builtins()` reads from `get_builtins_commands_dir()` (defined in `program/settings/paths.py`) and returns the result as a plain list, making it easy to inspect or extend the builtin set without constructing a registry.

Aliases are registered as additional keys pointing to the same `SlashCommandInfo`. `dispatch()` prints an "Unknown command" message and returns `False` for unrecognized commands.

Handlers are called with `(registry, args)`. If the handler returns a coroutine, it is awaited.

## Command discovery

`ResourceLoader` drives all command discovery. On `reload()` it loads commands from these directories in order (first-found wins on name collision):

| Directory | Path function | Purpose |
|---|---|---|
| `operator_use/builtins/commands/` | `get_builtins_commands_dir()` | Shipped built-in commands |
| `~/.operator/profiles/<name>/commands/` | `AgentProfile.commands_dir` | Active profile's commands |
| `<project>/.operator/commands/` | `<cwd>/.operator/commands` | Project-level custom commands (loaded when Operator runs in the repo) |
| Installed package `commands/` dirs | `get_packages_dir()` | From packages in `settings.packages` |

Built-in path functions are defined in `operator_use/settings/paths.py`.

A command file must export either `command = SlashCommandInfo(...)` or `commands = [SlashCommandInfo(...), ...]`.

```python
# .operator/commands/deploy.py
from operator_use.commands.types import SlashCommandInfo

async def _handle_deploy(registry, args):
    ...

command = SlashCommandInfo(
    name='deploy',
    description='Deploy the current branch.',
    handler=_handle_deploy,
)
```

The `CommandRegistry` is created with the full discovered list passed as `discovered=` (builtins + user commands already merged). Extension commands are then added via `register_from_extensions()` after that.

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
| `/cron [id]` | — | List scheduled cron jobs. Pass an ID prefix or name for details. |
| `/loop [interval] [message]` | — | Repeat a prompt on a fixed interval. No args lists active loops; `stop <name\|id>` cancels one. |
| `/new` | `/clear` | Start a new session (discards the current session history). |
| `/reload` | — | Reload all resources (tools, skills, commands, extensions) while keeping the active session history intact. |
| `/skills` | — | List all available skills. |
| `/start` | — | Introduce the agent and display welcome/help instructions. |
| `/steer <guidance>` | — | Inject guidance after the next tool call without interrupting the current turn. |
| `/stop` | `/cancel` | Immediately cancel the current agent operation and halt execution. |
| `/wiki [subcommand]` | — | Manage the profile knowledge base. Subcommands: `ingest`, `query`, `lint`, `dream`, `log`. No args lists pages. |
| `/help` | `/?` | List all available commands with descriptions and aliases. |

### /login

Enumerates OAuth providers from `LLM._providers.get_oauth_providers()`. Shows a numbered list with current login status. Accepts a provider ID directly as an argument to skip the prompt.

The login flow calls `ProviderAuthManager.login(provider_id, callbacks)`. The `callbacks` object receives the authorization URL (opened in the browser), a device code prompt if needed, and a completion signal. After the OAuth exchange, credentials are persisted to `auth.json`.

### /logout

Lists currently logged-in OAuth providers. Calls `ProviderAuthManager.logout(provider_id)`, which revokes the token server-side (if the provider supports it) and removes the stored credential.

### /auth

Prints one line per provider — OAuth providers first, then API-key providers. Each line shows the provider name, status (`logged in` / `not logged in` / `api key set`), the source (`stored`, `runtime`, `env`), and the label for env-sourced keys (e.g., `ANTHROPIC_API_KEY`).

### /compact

Calls `runtime.current_session.run_compaction(custom_instructions)`. Requires the agent to be idle — raises immediately if a turn is in progress. Prints "Nothing to compact." if the session cannot be compacted (already at a compaction entry, or no valid cut point).

### /new (/clear)

Calls `runtime.new_session()`. This triggers `session_shutdown` and `session_start` events, creates a fresh JSONL file, and resets the Agent's state.

### /reload

Calls `runtime.reload()`, which re-runs resource discovery for tools, skills, commands, hooks, and extensions **without** touching the active session. The conversation history is preserved exactly as it was.

What gets refreshed:
- `ResourceLoader.reload()` — re-scans all resource directories for file changes
- `Hooks` — cleared and re-registered from the reloaded hook files
- `ExtensionRuntime` — rebuilt with the newly discovered extensions
- `Engine.tools` — swapped to the reloaded tool list
- `CommandRegistry` — rebuilt with reloaded commands and re-registered extension commands

Use `/reload` when you have edited a tool, skill, hook, or extension file and want the running agent to pick up the changes without losing the current conversation.

### /skills

Lists all available skills discovered by `ResourceLoader` — builtins plus any skills in the active profile's `skills/` directory (`~/.operator/profiles/<name>/skills/`). Shows descriptions and any load diagnostics/warnings.

### /start

Introduces the agent, prints a welcome message, and provides quick starting instructions to guide the user.

### /steer

Injects specific instructions or guidance (e.g. `"/steer keep search results concise"`) to the running agent turn after the next tool call completes. Useful for steering an active turn without having to interrupt/cancel it.

### /stop (/cancel)

Sends an abort signal to the running engine. The agent will halt execution immediately at the next safe checkpoint (e.g., before executing the next tool or LLM inference step).

### /cron

Lists all scheduled cron jobs in the system, grouped by active vs. disabled status. Pass a job ID prefix or name (e.g., `/cron my-job`) to view full details for a specific cron job (message, next run, last run status, last error, and creation timestamps).

### /loop

Repeats a user prompt on a fixed interval using the cron scheduler. Requires `cron_enabled: true` in settings.

```
/loop <interval> <message>    — start a loop
/loop stop <name|id>          — stop a loop by name or ID prefix
/loop                         — list all active loops
```

Supported interval units: `ms`, `s`, `m`, `h`, `d` (e.g. `30s`, `5m`, `2h`).

Each loop is a cron job named `loop:<slug>`. Starting a loop with the same message slug replaces the existing one (no duplicates accumulate). Use `/loop stop` or the `/cron` command to cancel.

### /wiki

Manages the active profile's knowledge base. Requires an active profile (`--profile <name>`).

```
/wiki                      — list all knowledge pages with tags and always-load status
/wiki ingest <source>      — synthesize a source into knowledge pages (runs in background)
/wiki query <question>     — answer a question from the wiki using the agent
/wiki lint                 — check for contradictions and stale content (runs in background)
/wiki dream                — consolidate and deduplicate all pages (runs in background)
/wiki log                  — print the audit log (log.md)
```

`ingest`, `lint`, and `dream` invoke `WorkflowManager` in the background; use `/workflows <run_id>` to check progress.

`query` delegates directly to the runtime agent — it does **not** spawn a background workflow.

The page listing reads `index.yaml` to show `always-load` and tag metadata alongside each page name.

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
- [auth.md](./auth.md) — `ProviderAuthManager.login()` / `logout()` called by `/login` and `/logout`
- [extensions.md](./extensions.md) — Extension command registration

# Extensions

The extension system lets external code hook into agent lifecycle events, register tools and slash commands, and modify agent behaviour without touching the core. Extensions are Python files loaded from disk at startup and on reload.

## Architecture

```
ExtensionRuntime
  ├── _extensions: list[Extension]   ← loaded extension objects
  ├── _ctx: ExtensionContext          ← the Agent instance
  ├── _hooks: Hooks                  ← the system Hooks bus
  └── _errors: list[ExtensionError]  ← non-fatal load/dispatch errors
```

`ExtensionRuntime` is the single dispatch layer. Both loaded extensions and the system `Hooks` bus receive every event. Extensions are invoked first (in load order); then `Hooks` handlers run.

## Extension structure

An `Extension` object holds:

```python
@dataclass
class Extension:
    path: str                                    # source file path (for error attribution)
    source_info: SourceInfo                      # path + source label
    handlers: dict[str, list[Callable]]          # event_type → list of handlers
    tools: dict[str, RegisteredTool]             # tool_name → registered tool
    commands: dict[str, RegisteredCommand]       # command_name → slash command
    guardrails: dict[str, Guardrail]             # guardrail_name → Guardrail instance
    config: dict                                 # per-extension settings (from extension_list)
    # Provider registrations collected during factory execution
    inference_providers: list[Any]               # APIProvider | OAuthProvider instances
    inference_apis: dict[str, Any]               # LLM API classes keyed by name
    memory_providers: list[Any]                  # MemoryProvider descriptors
    memory_apis: dict[str, Any]                  # BaseMemoryAPI classes keyed by name
    subagent_profiles: list[Any]                 # SubagentProfile instances
```

## Writing an extension

An extension file must export a callable named `extension`. It receives an `ExtensionAPI` object and uses it to register handlers, tools, and commands:

```python
# ~/.operator/profiles/<name>/extensions/my_ext.py  (or <project>/.operator/extensions/my_ext.py)
from pydantic import BaseModel
from operator_use.extension.types import ToolDefinition
from operator_use.tool.types import ToolResult

class MyParams(BaseModel):
    text: str

async def _execute(params, invocation, ctx):
    return ToolResult.ok(invocation.id, params.text.upper())

def extension(api):
    # Read per-extension settings (configured in settings.json)
    threshold = api.config.get("threshold", 10)

    # Register an event handler
    api.on("session_start", lambda event, ctx: None)

    # Register a tool
    api.register_tool(ToolDefinition(
        name="my_tool",
        description="Does something useful.",
        parameters=MyParams,
        execute=_execute,
    ))

    # Register a slash command
    api.register_command("mytool", my_handler, description="Run my tool")
```

The factory may also be `async def extension(api)` for startup work such as fetching remote config.

### Practical Extension Examples

**Example 1: Git Guard** — Block dangerous git operations
```python
# ~/.operator/profiles/dev/extensions/git_guard.py
from operator_use.tool.types import ToolResult

async def _on_tool_call(event, ctx):
    # Event fired before tool execution
    if event.tool_name == "terminal":
        cmd = event.params.get("command", "")
        if "git push --force" in cmd or "git reset --hard" in cmd:
            # Reject with explanation
            return ToolResult.error(event.invocation.id, 
                "Force push/reset blocked by git_guard extension")
    return None  # Allow other tools

def extension(api):
    strict = api.config.get("strict", False)
    api.on("tool_call", _on_tool_call)
```

**Example 2: Session Tracker** — Log every turn to a file
```python
# ~/.operator/profiles/analyst/extensions/session_tracker.py
from datetime import datetime

async def _on_agent_end(event, ctx):
    timestamp = datetime.now().isoformat()
    # Access session from context
    session_id = ctx._session_manager.current_session_id()
    # Log to file
    with open("/tmp/sessions.log", "a") as f:
        f.write(f"{timestamp} | session {session_id}\n")

def extension(api):
    api.on("agent_end", _on_agent_end)
```

**Example 3: Custom Tool + Command** — Calculator with memoization
```python
# ~/.operator/profiles/math/extensions/calc_tool.py
from pydantic import BaseModel
from operator_use.tool.types import ToolResult
from operator_use.extension.types import ToolDefinition

cache = {}

class CalcParams(BaseModel):
    expression: str

async def _execute(params, invocation, ctx):
    expr = params.expression
    if expr in cache:
        return ToolResult.ok(invocation.id, f"{expr} = {cache[expr]} (cached)")
    
    try:
        result = eval(expr)  # Simple eval for demo
        cache[expr] = result
        return ToolResult.ok(invocation.id, f"{expr} = {result}")
    except Exception as e:
        return ToolResult.error(invocation.id, str(e))

async def _calc_command(params, ctx):
    # Slash command handler
    expr = params.get("expr", "2+2")
    result = await _execute(CalcParams(expression=expr), None, ctx)
    return f"Calc: {result.output}"

def extension(api):
    api.register_tool(ToolDefinition(
        name="calc",
        description="Evaluate a math expression",
        parameters=CalcParams,
        execute=_execute,
    ))
    api.register_command("calc", _calc_command, description="Quick calculation")
```

**Example 4: Async Startup** — Initialize on session start
```python
# ~/.operator/profiles/api/extensions/api_auth.py
async def _init_auth(event, ctx):
    api_key = api.config.get("api_key")
    if api_key:
        # Initialize external service
        await ctx._resources.set("api_client", init_api_client(api_key))
        print(f"API client ready")

def extension(api):
    api.on("session_start", _init_auth)
```

Configure in `~/.operator/settings.json`:
```json
{
  "extension_list": [
    {"name": "git_guard", "enabled": true, "settings": {"strict": true}},
    {"name": "calc_tool", "enabled": true},
    {"name": "api_auth", "enabled": true, "settings": {"api_key": "sk-..."}}
  ]
}
```

## Extension API

`ExtensionAPI` is passed to the factory. It provides:

| Method / property | Purpose |
|---|---|
| `api.config` | Per-extension settings dict from `extension_list` in `settings.json` |
| `api.events` | The shared `EventBus` |
| `api.on(event, handler)` | Register an event handler |
| `api.register_tool(ToolDefinition)` | Register a tool the LLM can call |
| `api.register_command(name, handler, description?)` | Register a slash command |
| `api.register_guardrail(guardrail)` | Register a `Guardrail` instance; file-loaded names take precedence on collision |
| `api.register_provider(provider)` | Register a custom inference provider (`APIProvider` or `OAuthProvider`) |
| `api.register_llm_api(name, api_class)` | Register a custom `BaseLLMAPI` subclass under a string name |
| `api.register_memory_provider(provider)` | Register a custom `MemoryProvider` descriptor |
| `api.register_memory_api(name, api_class)` | Register a custom `BaseMemoryAPI` subclass under a string name |
| `api.register_subagent_profile(profile)` | Register a `SubagentProfile` available to the subagent tool |

`ctx` in event handlers is the `ExtensionContext` — the live `Agent` instance. See [agent.md](./agent.md) for what it exposes.

## Loading

`ResourceLoader` drives extension discovery. On `reload()` it scans for extension files in this order:

| Directory | Path function | Purpose |
|---|---|---|
| `operator_use/builtins/extensions/` | `get_builtins_extensions_dir()` | Shipped built-in extensions |
| `~/.operator/profiles/<name>/extensions/` | `AgentProfile.extensions_dir` | Active profile's extensions |
| `<project>/.operator/extensions/` | `<cwd>/.operator/extensions` | Project-level extensions (loaded when Operator runs in the repo) |
| Installed package `extensions/` dirs | `get_packages_dir()` | From packages in `settings.packages` |
| `ResourceLoaderOptions.additional_extension_dirs` | — | Programmatically injected extras |

All path functions are defined in `operator_use/settings/paths.py`.

Each file is executed in a sandboxed module. Files starting with `_` are skipped. Load errors are non-fatal: a file that raises on import is recorded as an `ExtensionError` and skipped. The rest of the extensions load normally.

## Per-extension configuration

Extensions are configured in `settings.json` under `extension_list`. Each entry can toggle the extension on or off and pass a settings dict that the extension reads via `api.config`:

```json
{
  "extensions": true,
  "extension_list": [
    {
      "path": "~/.operator/profiles/<name>/extensions/git_guard.py",
      "name": "git_guard",
      "enabled": true,
      "author": "jeomon",
      "source": "local",
      "settings": {
        "strict": true,
        "block_force_push": false
      }
    },
    {
      "path": "~/.operator/profiles/<name>/extensions/noisy.py",
      "name": "noisy",
      "enabled": false
    }
  ]
}
```

**`extensions`** (top-level bool) — global kill switch. Set to `false` to disable all extensions. Defaults to `true`.

**`extension_list`** — per-extension overrides. Each entry is matched to a discovered file by stem name (`name` field, or stem of `path` if `name` is omitted).

`ExtensionEntry` fields:

| Field | Type | Required | Purpose |
|---|---|---|---|
| `path` | `str` | yes | File path (metadata only — discovery still uses dirs) |
| `name` | `str` | no | Stem name used for matching; derived from path if omitted |
| `enabled` | `bool` | no | Defaults to `true` |
| `source` | `str` | no | Informational: `"local"`, `"git"`, etc. |
| `author` | `str` | no | Informational |
| `settings` | `dict` | no | Passed to the extension as `api.config` |

Entries with `enabled: false` are skipped during discovery. Extensions not listed in `extension_list` are loaded with an empty `config`.

### Settings manager API

```python
sm.is_extensions_enabled() -> bool              # global toggle (default True)
sm.set_extensions_enabled(enabled: bool)        # set global toggle

sm.get_extension_list() -> list[ExtensionEntry] # per-extension entries
sm.set_extension_list(entries: list[ExtensionEntry])
```

## Dispatch

`ExtensionRuntime.emit(event_type, event)` calls handlers in this order:

1. For each loaded `Extension` in load order:
   - For each handler registered for `event_type` in that extension: call it with `(event, ctx)`.
2. If the event has a `.type` attribute: call `self._hooks.emit(event)` and collect results.
3. Return all non-None results from both passes.

Errors in extension handlers are caught per-handler and appended to `_errors`. They do not abort the dispatch loop.

```python
async def emit(self, event_type: str, event: Any) -> list[Any]:
    results = []
    for ext in self._extensions:
        for handler in ext.handlers.get(event_type, []):
            try:
                result = handler(event, self._ctx)
                if inspect.isawaitable(result):
                    result = await result
                if result is not None:
                    results.append(result)
            except Exception:
                self._errors.append(ExtensionError(...))
    if hasattr(event, 'type'):
        hook_results = await self._hooks.emit(event)
        results.extend(r for r in hook_results if r is not None)
    return results
```

## Parallel dispatch

`emit_parallel()` fires all handlers and Hooks concurrently using `asyncio.gather`. Use this for fire-and-forget events where ordering does not matter. Most lifecycle events use `emit()` (sequential) because result ordering matters.

## Tool registration

Extension tools are merged with base tools in `Agent.invoke()`:

```python
base_tool_names = {t.name for t in self._engine.tools}
ext_tools = [
    ExtensionTool(rt.definition, self)
    for name, rt in self._extensions.get_tools().items()
    if name not in base_tool_names
]
```

Base tool names take priority. If an extension registers a tool with the same name as a built-in tool, the built-in wins.

`get_tools()` collects all tools from all extensions. Last-writer-wins across extensions for the same tool name.

## Command registration

Commands registered by extensions are merged into the `CommandRegistry` at Runtime creation time:

```python
self.commands.register_from_extensions(
    self._context.extension_runtime.get_commands()
)
```

Extension commands are dispatched by the Runtime alongside built-in slash commands.

## Provider registration

Extensions can register custom inference and memory providers so the rest of the system can select them by ID. Registration is collected during factory execution and applied by the Runtime before constructing any provider-dependent services.

### Inference providers

```python
from operator_use.inference.provider.types import APIProvider
from operator_use.inference.types import LLMOptions

def extension(api):
    api.register_provider(APIProvider(
        id="my-llm",
        name="My LLM",
        api="my_llm_api",          # name under which the API class is registered
        options=LLMOptions(base_url="http://localhost:8080"),
    ))
    api.register_llm_api("my_llm_api", MyLLMAPI)   # BaseLLMAPI subclass
```

`register_provider` appends to `Extension.inference_providers`. `register_llm_api` adds to `Extension.inference_apis`. At startup, `RuntimeContext.create()` applies them to the class-level `LLM._providers` and `LLM._apis` registries **before** constructing the `LLM` instance, so a user can set `"provider": "my-llm"` in settings and it resolves correctly.

### Memory providers

```python
from operator_use.memory.provider.types import MemoryProvider
from operator_use.memory.api.base import BaseMemoryAPI
from operator_use.memory.types import MemoryOptions

class MyMemoryAPI(BaseMemoryAPI):
    async def prefetch(self, query, *, session_id=""):
        return "recalled context"

def extension(api):
    api.register_memory_provider(MemoryProvider(
        id="my-memory",
        name="My Memory",
        api="my_memory_api",
        options=MemoryOptions(),
    ))
    api.register_memory_api("my_memory_api", MyMemoryAPI)
```

`RuntimeContext.create()` seeds `MemoryProviderRegistry` and `MemoryAPIRegistry` from builtins, merges in extension registrations, then passes both to `MemoryManager`. A user can select the custom provider with `"memory": {"provider": "my-memory"}` in settings.

### Subagent profiles

```python
from operator_use.subagent.profile import SubagentProfile
from pathlib import Path

def extension(api):
    api.register_subagent_profile(SubagentProfile(
        name="researcher",
        description="Deep research agent with web access.",
        tools=["web_search", "web_fetch", "read"],
        system_prompt="You are a focused research agent...",
        file_path=Path(__file__),
    ))
```

Profiles registered this way are merged into `SubagentManager` in `Runtime.__init__()` after file-discovered profiles are loaded. Extension profiles are additive — they do not shadow file-discovered profiles of the same name, which take precedence.

### Runtime collector methods

`ExtensionRuntime` exposes corresponding collector methods for the Runtime to call:

| Method | Returns |
|---|---|
| `get_providers()` | `list` of inference providers from all extensions |
| `get_llm_apis()` | `dict[name, class]` merged across all extensions (last-writer-wins) |
| `get_memory_providers()` | `list` of memory provider descriptors from all extensions |
| `get_memory_apis()` | `dict[name, class]` merged across all extensions (last-writer-wins) |
| `get_subagent_profiles()` | `list` of `SubagentProfile` from all extensions |

## Engine event re-dispatch

Engine fires low-level events through `options.on_event`. Agent intercepts this and re-dispatches to extensions directly (bypassing `ExtensionRuntime.emit`) to avoid double-dispatching through `Hooks`:

```python
async def _on_engine_event(self, event):
    event_type = getattr(event, 'type', None)
    for ext in self._extensions._extensions:
        for handler in ext.handlers.get(event_type, []):
            await handler(event, self)
```

This means Engine events (`agent_start`, `turn_start`, `message_end`, `tool_execution_*`, …) go directly to extension handlers but not through `Hooks`. If you need Engine events in Hooks, register them in Agent via `hooks.register('message_end', ...)` directly.

## Error tracking

`ExtensionRuntime.errors` returns the accumulated `ExtensionError` list. Each entry has:

```python
@dataclass
class ExtensionError:
    extension_path: str
    event: str
    error: str    # last line of the traceback
    stack: str    # full traceback
```

These are non-fatal. The extension system continues operating after any handler error.

## ResourceLoader

`ResourceLoader` handles the filesystem side of extension discovery. It is separate from `ExtensionRuntime` and responsible for:

- Discovering extension files on disk (including from installed packages)
- Filtering disabled extensions and injecting per-extension configs
- Loading skills, tools, context files, and system prompts

It does not dispatch events. After `ResourceLoader.reload()`, the caller rebuilds the system prompt and re-creates the `ExtensionRuntime` with the fresh load result.

## Related documents

- [packages.md](./packages.md) — Package installation and how packages contribute extensions
- [agent.md](./agent.md) — Agent as ExtensionContext, tool merging, event fan-out
- [hooks.md](./hooks.md) — Event types and result semantics
- [engine.md](./engine.md) — Engine events that flow through `_on_engine_event`

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
    path: str                              # source file path (for error attribution)
    handlers: dict[str, list[Callable]]   # event_type → list of handlers
    tools: dict[str, RuntimeToolDef]      # tool_name → tool definition
    commands: dict[str, CommandDef]       # command_name → slash command
    hooks_api: Any                        # the API object passed to the extension
```

## Loading

`ResourceLoader` drives extension discovery. On `reload()` it scans for extension files in this order:

1. `program/builtins/extensions/` — shipped built-in extensions
2. `<project>/.program/agent/extensions/` — project-level extensions
3. `~/.program/agent/extensions/` — global user extensions
4. Any additional directories passed via `ResourceLoaderOptions.additional_extension_dirs`

Each file is executed in a sandboxed module. The extension receives an `api` object through which it registers handlers and tools.

Load errors are non-fatal: a file that raises on import is recorded as an `ExtensionError` and skipped. The rest of the extensions load normally.

## Extension API

Inside an extension file, the extension interacts via an `api` object:

```python
def setup(api):
    # Register an event handler
    @api.on('agent_end')
    async def on_end(event, ctx):
        print(f"Turn complete: {len(event.messages)} messages")

    # Register a tool
    api.register_tool(
        name="my_tool",
        description="Does something useful",
        parameters={...},  # JSON Schema
        execute=my_execute_fn,
    )

    # Register a slash command
    api.register_command(
        name="mycommand",
        description="Custom command",
        handler=my_command_handler,
    )
```

`ctx` in event handlers is the `ExtensionContext` — the live `Agent` instance. See [agent.md](./agent.md) for what it exposes.

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
                result = await handler(event, self._ctx)
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

- Discovering extension files on disk
- Loading skills and context files
- Reading system prompt files

It does not dispatch events. After `ResourceLoader.reload()`, the caller rebuilds the system prompt and re-creates the `ExtensionRuntime` with the fresh load result.

## Related documents

- [agent.md](./agent.md) — Agent as ExtensionContext, tool merging, event fan-out
- [hooks.md](./hooks.md) — Event types and result semantics
- [engine.md](./engine.md) — Engine events that flow through `_on_engine_event`

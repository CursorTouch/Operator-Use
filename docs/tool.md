# Tools

Tools give the LLM the ability to perform actions. Each tool is a Python class with a name, description, JSON schema for its parameters, and an async `execute()` method. The Engine resolves tool calls from the LLM, validates arguments, and calls `execute()`.

## Tool interface

```python
class Tool(ABC):
    name: str
    description: str
    schema: type[BaseModel]          # Pydantic model for input validation
    kind: ToolKind
    execution_mode: ToolExecutionMode

    def validate(self, params: dict) -> tuple[bool, list[str]]: ...
    def to_json(self) -> dict: ...   # produces the tool spec sent to the LLM

    def is_available(self, context: ToolContext) -> bool:
        """Return False to exclude this tool when its backing service is unavailable."""
        return True

    @abstractmethod
    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
    ) -> ToolResult: ...
```

## Conditional availability

Override `is_available(context: ToolContext) -> bool` to exclude a tool from the
LLM's tool list when its backing service is absent or disabled in settings:

```python
class ProcessTool(Tool):
    def is_available(self, context: ToolContext) -> bool:
        sm = context.settings_manager
        if sm is not None and sm.settings.some_flag is False:
            return False
        return context.process_manager is not None
```

`_configure_context()` in the Runtime calls `is_available()` for every tool after
building the `ToolContext`. Tools that return `False` are removed from the engine
immediately and do not appear in the list sent to the LLM. A `runtime.reload()`
re-runs this filtering, so toggling a settings flag and reloading makes a tool
appear or disappear without restarting.

Built-in tools and their availability conditions:

| Tool | Available when |
|---|---|
| `cron` | `cron_enabled ≠ false` AND cron service present |
| `subagent` | `subagents_enabled ≠ false` AND subagent manager present |
| `workflow` | `workflows_enabled ≠ false` AND workflow manager present |
| `computer` | `computer_use.enabled ≠ false` AND desktop present |
| `browser` | `browser_use.enabled ≠ false` AND browser present |
| `memory` | `memory.enabled ≠ false` AND memory manager present |
| `mcp` | MCP manager present |
| `send` | Bus present |
| `process` | Process manager present |

## Defining a tool

Subclass `Tool` and implement `execute()`:

```python
from pydantic import BaseModel
from operator_use.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

class ReadFileParams(BaseModel):
    path: str

class ReadFileTool(Tool):
    def __init__(self):
        super().__init__(
            name="read_file",
            description="Read the contents of a file",
            schema=ReadFileParams,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel,
        )

    async def execute(self, invocation, callback=None, signal=None) -> ToolResult:
        path = invocation.params["path"]
        try:
            content = Path(path).read_text()
            return ToolResult.ok(invocation.id, content)
        except FileNotFoundError:
            return ToolResult.error(invocation.id, f"File not found: {path}")
```

**Throw for unexpected errors; return `ToolResult.error()` for expected failures.** Thrown exceptions are caught by the Engine and converted to error results automatically. Use `ToolResult.error()` when the failure is meaningful to the LLM (e.g., file not found). Use exceptions for bugs or unexpected states.

## ToolResult

```python
@dataclass
class ToolResult:
    id: str
    content: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    terminate: bool = False

    @classmethod
    def ok(cls, id, content, metadata=None) -> ToolResult: ...

    @classmethod
    def error(cls, id, content, metadata=None) -> ToolResult: ...
```

**`terminate=True`**: signals the Engine to skip the automatic follow-up LLM call after this batch. The loop only stops early when every result in the batch sets `terminate=True`. Use this for tools that definitively end a task (e.g., a `done` tool that signals task completion).

**`metadata`**: arbitrary key-value pairs attached to the result. Not sent to the LLM; available to `after_tool_call` hooks for logging or UI.

## ToolInvocation

```python
@dataclass
class ToolInvocation:
    id: str                    # matches the tool call ID from the LLM
    name: str                  # tool name
    params: dict[str, Any]     # parsed arguments
    cwd: str                   # working directory at invocation time
```

## ToolKind

```python
class ToolKind(str, Enum):
    Read    = "read"       # reads files or external data
    Edit    = "edit"       # modifies files in place
    Write   = "write"      # creates or overwrites files
    Execute = "execute"    # runs commands
    Web     = "web"        # makes network requests
    Unknown = "unknown"
```

`kind` is metadata for UIs and permission systems. The Engine does not use it internally.

## ToolExecutionMode

```python
class ToolExecutionMode(str, Enum):
    Sequential = "sequential"   # one at a time
    Parallel   = "parallel"     # concurrent with other tools
    Batch      = "batch"        # global default — per-tool mode decides
```

Set per-tool via the constructor. When the Engine's global mode is `Batch`, tools with `Parallel` mode run concurrently; tools with `Sequential` mode run one at a time. If any tool in a batch has `Sequential` mode, the entire batch runs sequentially.

See [engine.md](./engine.md) for the full batch execution logic.

## Streaming tool output

`tool_execution_update_callback` lets a tool stream partial results:

```python
async def execute(self, invocation, callback=None, signal=None) -> ToolResult:
    for chunk in stream_data():
        if callback:
            await callback(ToolResult.ok(invocation.id, chunk))
        if signal and signal.is_set():
            break
    return ToolResult.ok(invocation.id, "done")
```

Each `callback` call emits a `ToolExecutionUpdateEvent` which is forwarded by the gateway as `kind: 'tool_update'` in `OutgoingMessage` metadata. Channels (Discord, Telegram, Slack) handle this by editing the rolling status message in-place during execution. The final `execute()` return value is the authoritative result.

## Abort signal

`signal` is an `asyncio.Event`. Check `signal.is_set()` inside long-running operations:

```python
async def execute(self, invocation, callback=None, signal=None) -> ToolResult:
    for item in items:
        if signal and signal.is_set():
            return ToolResult.error(invocation.id, "Aborted")
        await process(item)
    return ToolResult.ok(invocation.id, "done")
```

The Engine does not cancel the coroutine; abort is cooperative.

## Input validation

Before calling `execute()`, the Engine calls `tool.validate(params)`. This runs Pydantic validation against the tool's schema. If validation fails, the Engine returns a `ToolResultContent(is_error=True)` to the LLM without calling `execute()`.

`to_json()` produces the tool specification sent to the LLM:

```python
{
    "name": "read_file",
    "description": "Read the contents of a file",
    "input_schema": { ... }   # JSON Schema from Pydantic
}
```

## Loading tools from files

`load_tool_from_file(path)` dynamically imports a Python file and reads either:

- `tool` — a `Tool` instance (or a zero-arg callable that returns one)
- `tools` — a list of `Tool` instances

```python
# my_tool.py
from operator_use.tool.types import Tool, ToolKind, ToolExecutionMode

class MyTool(Tool):
    ...

tool = MyTool()   # or: tools = [MyTool(), AnotherTool()]
```

`load_tools_from_dir(directory)` scans all `*.py` files (skipping files starting with `_`) and collects every tool found.

`load_tools(dirs)` scans multiple directories and deduplicates by name — first-found wins.

Load errors are non-fatal: `LoadToolsResult.errors` accumulates `ToolError` objects for files that raised on import or exported nothing valid.

## Tool discovery

`ResourceLoader` drives all tool discovery. On `reload()` it loads tools from these directories in order (first-found wins on name collision):

| Directory | Path function | Purpose |
|---|---|---|
| `operator_use/builtins/tools/` | `get_builtins_tools_dir()` | Shipped built-in tools |
| `~/.operator/profiles/<name>/tools/` | `AgentProfile.tools_dir` | Active profile's tools |
| `<project>/.operator/tools/` | `<cwd>/.operator/tools` | Project-level custom tools (loaded when Operator runs in the repo) |
| `ResourceLoaderOptions.additional_tool_dirs` | — | Programmatically injected extras |

All path functions are defined in `program/settings/paths.py`.

Drop a `.py` file exporting `tool = MyTool()` into any of those directories and it is picked up automatically on the next reload. The built-in tools (`read`, `write`, `edit`, `grep`, `glob`, `ls`, `terminal`, `computer`, `browser`, `web_fetch`, `web_search`, `memory`, `mcp`, `cron`, `subagent`, `workflow`, `process`, `send`, `skill`, `todo`, `control_center`) live in `operator_use/builtins/tools/`.

## Extension tools

Extensions register tools through the extension API. Agent merges them with base tools at turn start, with base tool names taking priority over extension tool names.

Extension tools are wrapped in `ExtensionTool`, which holds a tool definition and a reference to the `ExtensionContext`. The execute call is forwarded to the extension's registered handler.

## Related documents

- [engine.md](./engine.md) — Tool execution modes, before/after hooks, abort
- [extensions.md](./extensions.md) — Extension tool registration and merging
- [hooks.md](./hooks.md) — `tool_call` and `tool_result` hook events

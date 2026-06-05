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

Drop a `.py` file exporting `tool = MyTool()` into any of those directories and it is picked up automatically on the next reload. The built-in tools (`read`, `write`, `edit`, `grep`, `glob`, `ls`, `terminal`, `computer`, `browser`, `web_fetch`, `web_search`, `memory`, `mcp`, `cron`, `subagent`, `workflow`, `process`, `send`, `skill`, `todo`, `control_center`, `session`) live in `operator_use/builtins/tools/`.

## Extension tools

Extensions register tools through the extension API. Agent merges them with base tools at turn start, with base tool names taking priority over extension tool names.

Extension tools are wrapped in `ExtensionTool`, which holds a tool definition and a reference to the `ExtensionContext`. The execute call is forwarded to the extension's registered handler.

## Related documents

- [engine.md](./engine.md) — Tool execution modes, before/after hooks, abort
- [extensions.md](./extensions.md) — Extension tool registration and merging
- [hooks.md](./hooks.md) — `tool_call` and `tool_result` hook events
- [session.md](./session.md) — Session persistence, branching, and context reconstruction

---

# Built-in Tool Reference

This section documents the purpose, usage, and behavior of each built-in tool.

## File & Data Tools

### `read`
**Purpose**: Read file contents with optional line-range selection.

**Parameters**:
- `path` (str): File path (absolute or relative)
- `limit` (int, optional): Maximum lines to return
- `offset` (int, optional): Starting line offset (0-based)

**Returns**: File content with line numbers and file hash (for edit validation).

**Use Cases**:
- Inspecting source code before editing
- Reading configuration files
- Checking log files

**Availability**: Always available.

---

### `write`
**Purpose**: Create new files or completely overwrite existing ones.

**Parameters**:
- `path` (str): File path (creates parent directories as needed)
- `content` (str): Full file content
- `overwrite` (bool, default=True): Prevent accidental overwrites if False

**Returns**: Confirmation of write operation.

**Use Cases**:
- Creating new scripts or config files
- Overwriting entire files (use `edit` for partial changes)

**Safety**: Parent directories are created automatically. Set `overwrite=False` to prevent data loss.

**Availability**: Always available.

---

### `edit`
**Purpose**: Surgically modify specific lines or ranges without rewriting entire files.

**Parameters**:
- `path` (str): File path
- `file_hash` (str): 4-hex hash from `read` output (prevents stale edits)
- `edits` (list): Array of edit operations (replace, delete, insert_before, insert_after, insert_head, insert_tail)

**Edit Operations**:
- `replace`: Replace lines `start_line..end_line` with new content
- `delete`: Remove lines `start_line..end_line`
- `insert_before`: Insert before `start_line`
- `insert_after`: Insert after `start_line`
- `insert_head`: Insert at file beginning
- `insert_tail`: Insert at file end

**Returns**: Confirmation with modified line numbers.

**Note**: All line numbers refer to the ORIGINAL file; edits are applied bottom-up (highest line first) so earlier anchors remain valid.

**Availability**: Always available.

---

### `ls`
**Purpose**: List files and directories in a folder (sorted alphabetically).

**Parameters**:
- `path` (str, default='.'): Directory path

**Returns**: Directory contents (subdirectories first, then files).

**Use Cases**:
- Exploring directory structure
- Checking file existence
- Understanding project layout

**Availability**: Always available.

---

### `glob`
**Purpose**: Find files matching a pattern (e.g., `*.py`, `**/*.json`).

**Parameters**:
- `pattern` (str): Glob pattern
- `path` (str, default='.'): Search root
- `limit` (int, default=1000): Maximum results

**Returns**: List of matching file paths.

**Use Cases**:
- Finding all Python files in a project
- Locating config files recursively
- Batch file operations

**Availability**: Always available.

---

### `grep`
**Purpose**: Search for text patterns across files (regex or literal).

**Parameters**:
- `pattern` (str): Search pattern
- `path` (str, default='.'): Search root
- `glob` (str, optional): Filter files by pattern (e.g., `*.ts`)
- `literal` (bool, default=False): Treat pattern as literal string (not regex)
- `ignore_case` (bool, default=False): Case-insensitive search
- `context` (int, default=0): Lines of context before/after match
- `limit` (int, default=100): Maximum matches to return

**Returns**: Matched lines with file paths and line numbers.

**Use Cases**:
- Finding function definitions
- Locating error messages in logs
- Code navigation and refactoring

**Availability**: Always available.

---

## Execution & Process Tools

### `terminal`
**Purpose**: Run shell commands and return output.

**Parameters**:
- `cmd` (str): Shell command (chain with `&&` for sequential execution)
- `cwd` (str, optional): Working directory
- `timeout` (int, default=10, max=60): Seconds before timeout (process is backgrounded, not killed)
- `detached` (bool, default=False): Run in background and return immediately

**Returns**: stdout, stderr, and exit code.

**Use Cases**:
- Git operations (`git clone`, `git commit`)
- Package management (`pip install`, `npm start`)
- Running test suites
- Compiling code

**Note**: If timeout expires, the process is moved to background (use `process` tool to check output).

**Availability**: Always available.

---

### `process`
**Purpose**: Manage long-running background processes or spawn background agents.

**Parameters**:
- `action` (str): `start`, `spawn_agent`, `write`, `list`, `get`, `stop`, `output`

**Actions**:
- `start`: Launch shell command as background process
- `spawn_agent`: Spawn background AI agent with initial prompt
- `write`: Send follow-up prompt to running agent
- `list`: List all processes (optionally filtered by status)
- `get`: Get detailed status of one process
- `stop`: Terminate a process (SIGTERM → SIGKILL)
- `output`: Read last N bytes of process log

**Use Cases**:
- Running servers (e.g., `npm start`, `python -m http.server`)
- Long-running data processing
- Watchers (e.g., file monitors, CI/CD)
- Delegating tasks to background agents

**Availability**: Requires process manager (always available in standard setup).

---

## Web & Network Tools

### `web_search`
**Purpose**: Search the web and get ranked results.

**Parameters**:
- `query` (str): Search query
- `mode` (str, default='text'): `text`, `news`, `images`, `videos`, or `books`
- `max_results` (int, default=10): Number of results

**Returns**: Ranked list of results with URLs and snippets.

**Use Cases**:
- Research questions ("latest Python releases")
- Finding documentation
- Staying current on news

**Availability**: Requires internet connection.

---

### `web_fetch`
**Purpose**: Fetch and extract content from a specific URL.

**Parameters**:
- `url` (str): Full URL (must start with http/https)
- `prompt` (str, optional): If provided, LLM extracts only relevant parts (e.g., "current temperature")
- `timeout` (int, default=10): Request timeout in seconds

**Returns**: Page content (raw if no prompt, filtered if prompt provided).

**Use Cases**:
- Reading documentation pages
- Fetching API specs
- Scraping structured data
- Extracting information from long articles

**Availability**: Requires internet connection.

---

## Browser & Desktop Tools

### `browser`
**Purpose**: Control a Chrome/Edge browser via CDP (Chrome DevTools Protocol).

**Parameters** (varies by action):
- `action` (str): `open`, `close`, `goto`, `back`, `forward`, `click`, `type`, `key`, `scroll`, `menu`, `upload`, `tab`, `wait`, `script`, `scrape`, `download`
- `url` (str): URL for `open` or `goto`
- `x`, `y` (int): Coordinates for click/move
- `text` (str): Text to type
- `script` (str): JavaScript to execute

**Use Cases**:
- Logging into web applications
- Filling and submitting forms
- Scraping dynamic (JavaScript-rendered) content
- Automating web workflows

**Availability**: Requires `browser_use.enabled ≠ false` AND browser present.

---

### `computer`
**Purpose**: Control the desktop (click, type, screenshot, launch apps).

**Parameters** (varies by action):
- `action` (str): `open`, `close`, `click`, `type`, `wait`, `app`, `scroll`, `move`, `drag`, `shortcut`
- `loc` (list): [x, y] coordinates
- `name` (str): App name/bundle ID for `app` action
- `text` (str): Text to type
- `shortcut` (str): Keyboard shortcut (e.g., `cmd+c`, `ctrl+s`)

**Use Cases**:
- GUI automation (clicking buttons, filling fields)
- Launching applications
- Taking screenshots
- Automating desktop workflows

**Availability**: Requires `computer_use.enabled ≠ false` AND desktop present.

---

## Memory & Knowledge Tools

### `memory`
**Purpose**: Long-term memory across sessions (provider-agnostic).

**Actions**:
- `search`: Retrieve relevant memories by query
- `remember`: Store a durable fact
- `forget`: Remove a memory (if provider supports deletion)
- `reflect`: Synthesize an answer across stored memories

**Parameters**:
- `action` (str): Search, remember, forget, reflect
- `query` (str): For search/reflect
- `content` (str): For remember
- `memory_id` (str): For forget
- `limit` (int, default=5): Max search results

**Use Cases**:
- Remembering user preferences across sessions
- Storing project decisions and conventions
- Recalling past errors and solutions

**Best Practices**:
- Store stable facts, not raw transcripts
- Never store API keys or secrets in memory
- Use `search` at task start to fetch relevant context

**Availability**: Requires `memory.enabled ≠ false` AND memory manager present.

---

### `knowledge`
**Purpose**: Access curated reference material (API specs, guidelines, docs).

**Actions**:
- `list`: Show all pages with preview
- `query`: Answer a question using knowledge base
- `add`: Write content directly to a page
- `ingest`: Synthesize a source (URL, file, or text)
- `lint`: Check for contradictions
- `consolidate`: Deduplicate entries
- `log`: Return audit log of operations

**Use Cases**:
- Storing API documentation
- Maintaining company guidelines
- Archiving project specifications

**Availability**: Always available (profile-scoped).

---

## Task & Workflow Tools

### `todo`
**Purpose**: Manage an ordered session task list (3+ steps).

**Parameters**:
- `todos` (list): Array of task items with `id`, `content`, `status`
- `merge` (bool, default=False): If True, merge with existing items; if False, replace entire list

**Statuses**: `pending`, `in_progress`, `completed`, `blocked`, `cancelled`, `skipped`

**Use Cases**:
- Complex multi-step projects
- Tracking progress during long tasks
- Organizing work with dependencies

**Best Practices**:
- Keep only one item `in_progress` at a time
- Use `blocked` when waiting on external input
- Mark completed items immediately

**Availability**: Always available.

---

### `workflow`
**Purpose**: Create and run Python workflow scripts (multi-step pipelines).

**Actions**:
- `create`: Generate a new workflow from description
- `run`: Start a workflow (returns run_id immediately)
- `list`: Show active and recent runs
- `status`: Get detailed status of a run
- `cancel`: Stop a running workflow
- `discover`: List all available workflows
- `delete`: Permanently remove a workflow

**Parameters**:
- `name` (str): Workflow name (used as filename)
- `description` (str): What the workflow should do
- `args` (dict): Key-value arguments passed to workflow
- `deliver` (bool, default=True): Inject result back when done

**Patterns**:
- `classify-and-act`
- `fan-out-and-synthesize`
- `adversarial-verify`
- `generate-and-filter`
- `tournament`
- `loop-until-done`

**Use Cases**:
- Automating repetitive multi-agent tasks
- Data processing pipelines
- Complex decision-making workflows

**Note**: After calling `run`, END YOUR TURN—result is injected automatically.

**Availability**: Requires `workflows_enabled ≠ false` AND workflow manager present.

---

### `cron`
**Purpose**: Schedule recurring jobs (fixed interval or cron expression).

**Actions**:
- `add`: Create a new scheduled job
- `update`: Modify an existing job
- `remove`: Delete a job
- `enable`/`disable`: Pause/resume a job
- `list`: Show all jobs

**Parameters**:
- `name` (str): Human-readable label
- `message` (str): Prompt to send when job fires
- `schedule_mode` (str): `every` (fixed interval) or `cron` (expression)
- `interval_ms` (int): Interval for `every` mode
- `expr` (str): Cron expression for `cron` mode (5 fields: minute hour dom month dow)
- `tz` (str): IANA timezone (default: UTC)
- `channel_id` (str): Send to external channel (telegram, discord, slack)
- `deliver` (bool): Send directly to channel instead of agent

**Use Cases**:
- Daily/weekly reminders
- Periodic backups
- Scheduled reports
- Monitoring tasks

**Best Practices**:
- Always confirm with user before creating recurring jobs
- Use timezone to match user's location
- Test the message before scheduling

**Availability**: Requires `cron_enabled ≠ false` AND cron service present.

---

## Multi-Agent Tools

### `subagent`
**Purpose**: Spawn isolated agents for parallel or background work.

**Actions**:
- `create`: Delegate a task to a new background subagent (END YOUR TURN after)
- `list`: Show all subagents (running and finished)
- `status`: Get detailed status and result of a subagent
- `cancel`: Stop a running subagent
- `profiles`: List available named subagent profiles

**Parameters**:
- `task` (str): Full description of the task
- `label` (str): Short human-readable label
- `profile` (str): Named subagent profile (required unless `fork=True`)
- `fork` (bool, default=False): Inherit conversation context (no profile needed)
- `depends_on` (list): Task IDs that must complete first

**Profiles**: Call `profiles` action FIRST to get exact list (never guess).

**Use Cases**:
- Research while main agent works on something else
- Parallel file processing
- Delegating to specialists (coding agent, research agent, etc.)

**Note**: After calling `create`, END YOUR TURN—result is injected automatically.

**Availability**: Requires `subagents_enabled ≠ false` AND subagent manager present.

---

### `peer_agent`
**Purpose**: Delegate tasks to another named profile agent (persistent session).

**Actions**:
- `list`: Show available peers and their status
- `run`: Send task to peer and wait for result
- `spawn`: Create a persistent named session (returns session_id)
- `send`: Continue an existing session
- `sessions`: List peer sessions opened by this agent
- `status`: Check status of a detached run
- `cancel`: Stop a detached run

**Parameters**:
- `name` (str): Target peer profile name
- `task` (str): Message/task to send
- `session_id` (str): For continuing sessions
- `detached` (bool): Run in background

**Use Cases**:
- Delegating to a persistent coding agent
- Collaborative multi-agent workflows
- Specialist agents with ongoing context

**Availability**: Requires peer agents configured in settings.

---

### `team`
**Purpose**: Create and manage persistent multi-agent teams (shared goal).

**Actions**:
- `create`: Create a new named team
- `spawn`: Add a worker to the team
- `send`: Send message to team member's inbox
- `inbox`: Read pending messages for a member
- `status`: Show team members and their status
- `dissolve`: Mark a team as dissolved
- `list`: List all teams

**Parameters**:
- `team_name` (str): Name of the team
- `member_name` (str): Name for new member
- `role` (str): Subagent profile for member
- `task` (str): Initial task for member
- `agent_id` (str): Task ID of target member
- `message` (str): Message to send to inbox

**Use Cases**:
- Coordinating multiple agents on a shared project
- Long-running team-based workflows
- Asynchronous multi-agent collaboration

**Availability**: Requires subagent manager present.

---

## Communication & Session Tools

### `send`
**Purpose**: Deliver content to user outside normal response flow.

**Actions**:
- `file`: Upload a local file (any format)
- `intermediate`: Push mid-turn status update
- `react`: Add emoji reaction to message

**Parameters**:
- `path` (str): File path for `file` action
- `caption` (str): Optional caption for `file`
- `text` (str): Status message for `intermediate`
- `emoji` (str): Emoji character for `react`
- `reply` (bool, default=False): Reply to triggering message

**Use Cases**:
- Share results as files
- Acknowledge receipt with reactions
- Provide progress updates on long tasks

**Best Practices**:
- Use `intermediate` every 30–60 seconds on long tasks
- Don't use `send` for final answers (just respond normally)
- Use `react` instead of short text replies in group chats

**Availability**: Requires bus present (always available).

---

### Session Management (Server-side)
**Purpose**: Manage session persistence, branching, and context reconstruction.

**Note**: The `session` tool is primarily server-side and not directly exposed to agents. However, understanding session architecture is critical for agent stability and multi-turn conversations:

**Key Concepts**:
- **Persistence**: Session data stored as JSONL files (one entry per action)
- **Branching**: Tree structure allows rewinding to any point without losing history
- **Context Reconstruction**: Compaction + branch walking builds the LLM's message context
- **Lazy Flushing**: Sessions only persist to disk after first assistant message

**Entry Types**:
| Type | Purpose |
|------|---------|
| `SessionHeader` | First entry; holds session ID and working directory |
| `MessageEntry` | One `AgentMessage` (user, assistant, or tool-result) |
| `CompactionEntry` | Marks compaction point; carries summary and `first_kept_entry_id` |
| `BranchEntry` | Branch-navigation point with summary of old branch |
| `LeafEntry` | Durable navigation pointer (`target_id` becomes new leaf) |
| `LabelEntry` | Attaches human label to any entry |
| `ThinkingLevelChangeEntry` | Records thinking-level switch |
| `ModelChangeEntry` | Records model switch |
| `SessionInfoEntry` | Stores session name |
| `CustomInfoEntry` | Extension-writable metadata |
| `CustomMessageEntry` | Extension-writable message |

**Session Lifecycle**:
1. **Creation**: `SessionManager.create(cwd)` starts in-memory
2. **First Flush**: Persisted to disk when first `AssistantMessage` arrives
3. **Append-Only**: New entries appended with `open("a")` — O(1) per entry
4. **Branching**: `branch(from_id)` moves leaf pointer without discarding entries
5. **Forking**: `create_branched_session(leaf_id)` copies path to new file
6. **Compaction**: Older entries summarized and optionally removed (via `session.md`)

**Factory Methods**:
- `SessionManager.create(cwd)`: Start fresh
- `SessionManager.open(path)`: Load existing session
- `SessionManager.continue_recent(cwd)`: Resume most recent
- `SessionManager.in_memory(cwd)`: Testing (no persistence)
- `SessionManager.fork_from(source, target_cwd)`: Copy with new ID

**Availability**: Core runtime feature (not directly called by agents).

---

## Utility Tools

### `control_center`
**Purpose**: Inspect and update runtime settings.

**Actions**:
- `get`: Read current values (omit `key` to see all)
- `set`: Update a setting
- `reboot`: Full system restart

**Common Keys**:
- `model`: Current session LLM model (takes effect next turn)
- `provider`: Current session LLM provider
- `browser_use`: Enable/disable browser automation
- `computer_use`: Enable/disable desktop control
- `cron`: Enable/disable cron scheduler
- `extensions`: Enable/disable all extensions
- `default_model`: Default model for new sessions
- `default_provider`: Default provider for new sessions

**Use Cases**:
- Switching models mid-session
- Toggling features based on task requirements
- Restarting after configuration changes

**Availability**: Always available.

---

### `skill`
**Purpose**: View and manage reusable skill guides (user-defined procedures).

**Actions**:
- `view`: Load a skill's full instructions
- `create`: Create new skill
- `edit`: Replace skill content
- `patch`: Find-and-replace within skill
- `delete`: Remove a skill
- `write_file`: Add support file (references/, templates/, scripts/)
- `remove_file`: Delete support file

**Use Cases**:
- Encoding best practices (e.g., "How to debug Flask apps")
- Reusable procedures for repeated tasks
- Team guidelines and standards

**Best Practices**:
- Always `view` a skill before following it
- Keep skills modular and specific
- Include examples and common pitfalls

**Availability**: Always available (profile-scoped).

---

## Tool Summary Table

| Tool | Kind | Mode | Purpose |
|------|------|------|---------|
| `read` | Read | Parallel | Read file contents |
| `write` | Write | Parallel | Create/overwrite files |
| `edit` | Edit | Parallel | Modify specific lines |
| `ls` | Read | Parallel | List directory contents |
| `glob` | Read | Parallel | Find files by pattern |
| `grep` | Read | Parallel | Search files for text |
| `terminal` | Execute | Sequential | Run shell commands |
| `process` | Execute | Sequential | Manage background processes |
| `web_search` | Web | Sequential | Search the web |
| `web_fetch` | Web | Sequential | Fetch URL content |
| `browser` | Execute | Sequential | Control web browser |
| `computer` | Execute | Sequential | Control desktop |
| `memory` | Read | Parallel | Long-term memory |
| `knowledge` | Read | Parallel | Reference materials |
| `todo` | Execute | Parallel | Session task list |
| `workflow` | Execute | Sequential | Multi-step pipelines |
| `cron` | Execute | Sequential | Schedule recurring jobs |
| `subagent` | Execute | Sequential | Spawn background agents |
| `peer_agent` | Execute | Sequential | Delegate to specialists |
| `team` | Execute | Sequential | Multi-agent teams |
| `send` | Execute | Parallel | Send files/updates |
| `control_center` | Execute | Parallel | Manage settings |
| `skill` | Read | Parallel | View reusable procedures |

---

## Tool Selection Guide

**For file operations**: Use `read` → `edit` (not `write`) for targeted changes.

**For web content**: Use `web_search` first, then `web_fetch` to read specific URLs.

**For long tasks**: Use `send` with `intermediate` updates every 30–60 seconds.

**For parallel work**: Spawn `subagent` with appropriate profile.

**For memory**: Use `memory.search()` at task start; `memory.remember()` when learning.

**For automation**: Use `cron` for recurring, `workflow` for multi-step logic.

**For complex workflows**: Use `team` for coordinated multi-agent work.

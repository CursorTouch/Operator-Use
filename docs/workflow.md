# Workflows

Workflows are Python files that orchestrate multi-step tasks using a built-in async DSL. The `workflow` tool runs them as background tasks and delivers results back to the calling session.

## Architecture

```
operator_use/workflow/
  types.py      ← WorkflowMeta, WorkflowRunRecord, WorkflowStatus, WorkflowContext,
                  WorkflowInvocation, WorkflowJournal, Workflow (ABC)
  manager.py    ← WorkflowManager — discovery, invocation, status tracking
  load.py       ← WorkflowLoader — file discovery, meta parsing
  execute.py    ← DSL globals injection
  context.py    ← WorkflowExecuteContext, Budget, phase context manager
```

## Workflow file format

A workflow is a `.py` file with a top-level `meta` dict and an `async def run()` entry point:

```python
# ~/.operator/profiles/<name>/workflows/research.py

meta = {
    "name": "research",
    "description": "Research a topic and produce a structured report.",
    "when_to_use": "User asks for research or background on a subject.",
    "phases": [
        {"name": "gather", "description": "Find sources"},
        {"name": "write",  "description": "Write report"},
    ],
}

async def run():
    topic = args.get("topic", "AI safety")

    async with phase("gather"):
        log(f"Gathering sources on: {topic}")
        sources = await agent(f"Find 5 authoritative sources on {topic}.")

    async with phase("write"):
        log("Writing report…")
        report = await agent(
            f"Write a structured markdown report on {topic} using:\n{sources}"
        )

    return report
```

## DSL globals

These names are injected at runtime — **do not import them**:

| Global | Signature | Description |
|---|---|---|
| `agent` | `await agent(prompt, schema=None, system=None, tools=None, resume=False, stall_ms=180000, max_retries=5)` | Run an LLM agent call. Returns `str` (no schema) or a Pydantic model instance. A call that doesn't finish within `stall_ms` is cancelled and retried up to `max_retries` times, then raises `TimeoutError`. |
| `parallel` | `await parallel(*thunks, concurrency=5, return_exceptions=False)` | Run zero-argument async callables concurrently; returns list of results. Default fail-fast (cancels siblings, re-raises); with `return_exceptions=True` it never rejects and each failed slot holds its exception. |
| `pipeline` | `await pipeline(items, *stages, concurrency=5)` | Process items through a list of sync or async transform functions. |
| `workflow` | `await workflow(name, args=None)` | Run another workflow inline and return its result. One level deep only; shares the caller's run record (unified log + shared agent-call cap). |
| `phase` | `async with phase("name"):` | Label the current phase in the run status. |
| `log` | `log("message")` | Append a timestamped line to the run log. |
| `budget` | `budget.remaining()` / `budget.spent()` / `budget.exhausted()` | Soft, advisory turn budget for loop guards (not enforced — see Limits). |
| `args` | `dict` | Key-value arguments passed at invocation. |

## `agent()` schema mode

When `schema` is a Pydantic model class, `agent()` instructs the LLM to respond in that shape:

```python
from pydantic import BaseModel

class Summary(BaseModel):
    title: str
    bullet_points: list[str]

result: Summary = await agent("Summarise this document.", schema=Summary)
print(result.title)
```

## Nested workflows

A workflow can run another inline via `workflow()` and use its result:

```python
async def run():
    cleaned = await workflow("normalize", {"text": args["text"]})
    return await agent(f"Summarise:\n{cleaned}")
```

Nesting is **one level deep only** — a nested workflow that calls `workflow()` again raises `RuntimeError`. The nested run shares the caller's run record, so its log lines and `agent()` calls are unified with the parent (and count against the same cap). Both file-based and class-based workflows can be invoked.

## Limits & safety

| Limit | Default | Behavior |
|---|---|---|
| `max_agent_calls` | `1000` | Hard runaway-loop guard. Once a run (including its nested workflows) has made this many `agent()` calls, the next one raises `WorkflowAgentCapError`. Override via `args["max_agent_calls"]`. |
| `stall_ms` (per `agent()`) | `180000` | A call that doesn't finish in this window is cancelled and retried. |
| `max_retries` (per `agent()`) | `5` | Stall retries before `agent()` raises `TimeoutError`. |
| run wall-clock | `1800s` | Whole-run timeout enforced by `WorkflowManager`. |

`budget` is **advisory only** — it is incremented per `agent()` call for use in loop conditions (`while budget.remaining() > 10: …`) but never auto-enforces. The hard stop is `max_agent_calls`.

## Tool actions

```python
# run a workflow in the background
{ "action": "run", "name": "research", "args": { "topic": "LLM context windows" } }
# → returns run_id immediately

# generate a new workflow from a description
{ "action": "create", "name": "summarize",
  "description": "Summarize a set of documents and produce key takeaways." }

# generate a workflow that does NOT inject its result back into the session
{ "action": "create", "name": "export-log",
  "description": "Export session log to file.", "deliver": false }

# list available workflows
{ "action": "discover" }

# list active and recent runs
{ "action": "list" }

# check run status
{ "action": "status", "run_id": "<id>" }

# cancel a running workflow
{ "action": "cancel", "run_id": "<id>" }

# permanently delete a workflow file
{ "action": "delete", "name": "old-workflow" }
```

Workflow files are stored in the active profile's `workflows/` directory (e.g. `~/.operator/profiles/<name>/workflows/`). When no profile is active, they fall back to a temporary directory.

## WorkflowRunRecord

```python
@dataclass
class WorkflowRunRecord:
    run_id: str
    workflow_name: str
    status: WorkflowStatus       # running | completed | failed | cancelled
    started_at: datetime
    finished_at: datetime | None
    result: Any                  # return value of run()
    error: str | None
    log_lines: list[str]
    agent_calls: int
    current_phase: str | None
    channel: str | None          # reply channel (same pattern as SubagentManager)
    chat_id: str | None
    deliver: bool = True         # inject result back into the calling session when done
```

## `deliver` flag

`WorkflowMeta.deliver` (default `True`) controls whether the workflow result is injected back into the calling session when the run completes. Set to `False` in `meta` for fire-and-forget workflows that write to files or external systems and don't need to report back:

```python
meta = {
    "name": "export-log",
    "description": "Export session log to a file.",
    "deliver": False,
}
```

When creating with the `workflow` tool, pass `"deliver": false` in the schema to embed this in the generated file.

## Code generation

`{ "action": "create" }` uses the LLM to write a new workflow file from a description, then saves it to the profile's `workflows/` directory. The generated file follows all DSL rules and is immediately available to run.

## Class-based workflows (`Workflow` ABC)

For programmatic workflows (not user-authored files), subclass `Workflow` from `operator_use.workflow.types`:

```python
from operator_use.workflow.types import Workflow, WorkflowContext, WorkflowInvocation

class MyWorkflow(Workflow):
    name = 'my-workflow'
    description = 'Does something useful.'
    when_to_use = 'User asks for something useful.'
    phases = [
        {'name': 'step1', 'description': 'First step'},
        {'name': 'step2', 'description': 'Second step'},
    ]

    async def execute(self, invocation: WorkflowInvocation, workflow_context: WorkflowContext) -> str:
        ctx = await self.build_context(invocation, workflow_context)
        async with ctx.phase('step1'):
            result = await ctx.agent('Do step 1.')
        async with ctx.phase('step2'):
            final = await ctx.agent(f'Do step 2 given: {result}')
        return final
```

`build_context()` constructs a `WorkflowExecuteContext` (from `context.py`) with all DSL globals (`agent`, `phase`, `log`, `args`, etc.) plus a `WorkflowRunRecord` and `WorkflowJournal`. The underlying `Subagent` is filtered to exclude `subagent` and `workflow` tools to prevent recursive nesting.

`WorkflowContext` (the lightweight dependency carrier):
```python
@dataclass
class WorkflowContext:
    llm: Any
    tools: list
```

`WorkflowInvocation` (analogous to `ToolInvocation`):
```python
@dataclass
class WorkflowInvocation:
    workflow_name: str
    args: dict[str, Any]    # keyword args for the workflow
    run_id: str             # auto-generated if not provided
```

`WorkflowJournal` (write-through SHA-256-keyed cache, moved from `journal.py` into `types.py`):
```python
journal.get(prompt, opts)          # None if not cached
journal.set(prompt, opts, result)  # write to memory and disk
```

## Discovery

`WorkflowLoader` scans the configured `workflows_dir` for `.py` files. A file is a valid workflow if it contains a top-level `meta` dict with at least `name` and `description`, and an `async def run()` function.

Workflow search paths (highest priority last):
1. Builtins (`operator_use/builtins/workflows/` if present)
2. Profile: `~/.operator/profiles/<name>/workflows/`

## Execution isolation

Each workflow run gets its own `WorkflowExecuteContext`. The `agent()` call runs a `Subagent` instance with the same tools as the parent agent (minus `subagent` and `workflow` to prevent recursive nesting). Per-run state (log, journal) is stored under `<tmpdir>/.operator-workflow-runs/<run_id>/`. Results are delivered back via the message bus (unless `deliver=False`).

## Settings

A structured `workflow` block sets per-run knob defaults (global and per-profile, via the settings merge). Explicit invocation `args` and per-call `agent(...)` params still override these.

```json
{
  "workflow": {
    "enabled": true,
    "max_agent_calls": 1000,
    "budget": 100,
    "concurrency": 5,
    "stall_ms": 180000,
    "max_retries": 5
  }
}
```

When `enabled` is `false`, the `workflow` tool is hidden from the LLM. `workflow.enabled` supersedes the legacy flat `workflows_enabled` flag (still read as a fallback when no `workflow` block is present). Toggle on/off via `control_center`:

```python
{ "action": "set", "key": "workflows_enabled", "value": true }
```

Precedence for the knobs: **per-call `agent()`/`parallel()` argument → invocation `args` → `workflow` settings block → built-in default.**

## Related documents

- [docs/profiles.md](./profiles.md) — Profile workflow directory
- [docs/team.md](./team.md) — Teams as an alternative coordination primitive
- [docs/acp.md](./acp.md) — ACP agents as external workflow executors

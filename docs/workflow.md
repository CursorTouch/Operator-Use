# Workflows

Workflows are Python files that orchestrate multi-step tasks using a built-in async DSL. The `workflow` tool runs them as background tasks and delivers results back to the calling session.

## Architecture

```
operator_use/workflow/
  types.py      ← WorkflowMeta, WorkflowRunRecord, WorkflowStatus
  manager.py    ← WorkflowManager — discovery, invocation, status tracking
  load.py       ← WorkflowLoader — file discovery, meta parsing
  execute.py    ← WorkflowContext, DSL globals injection
  journal.py    ← per-run log
  context.py    ← Budget, phase context manager
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
| `agent` | `await agent(prompt, schema=None, system=None, tools=None, resume=False)` | Run an LLM agent call. Returns `str` (no schema) or a Pydantic model instance. |
| `parallel` | `await parallel(*thunks, concurrency=5)` | Run zero-argument async callables concurrently; returns list of results. |
| `pipeline` | `await pipeline(items, *stages, concurrency=5)` | Process items through a list of sync or async transform functions. |
| `phase` | `async with phase("name"):` | Label the current phase in the run status. |
| `log` | `log("message")` | Append a timestamped line to the run log. |
| `budget` | `budget.remaining()` / `budget.spent()` / `budget.exhausted()` | Track turn budget. |
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

## Tool actions

```python
# run a workflow in the background
{ "action": "run", "name": "research", "args": { "topic": "LLM context windows" } }
# → returns run_id immediately

# generate a new workflow from a description
{ "action": "generate", "name": "summarize",
  "description": "Summarize a set of documents and produce key takeaways." }

# list available workflows
{ "action": "list" }

# check run status
{ "action": "status", "run_id": "<id>" }

# cancel a running workflow
{ "action": "cancel", "run_id": "<id>" }
```

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
```

## Code generation

`{ "action": "generate" }` uses the LLM to write a new workflow file from a description, then saves it to the profile's `workflows/` directory. The generated file follows all DSL rules and is immediately available to run.

## Discovery

`WorkflowLoader` scans the configured `workflows_dir` for `.py` files. A file is a valid workflow if it contains a top-level `meta` dict with at least `name` and `description`, and an `async def run()` function.

Workflow search paths (highest priority last):
1. Builtins (`operator_use/builtins/workflows/` if present)
2. Profile: `~/.operator/profiles/<name>/workflows/`

## Execution isolation

Each workflow run gets its own `WorkflowContext`. The `agent()` call runs a `Subagent` instance with the same tools as the parent agent (minus `subagent` and `workflow` to prevent recursive nesting). Results are delivered back via the message bus.

## Settings

```json
{ "workflows_enabled": true }
```

When `workflows_enabled` is `false`, the `workflow` tool is hidden from the LLM. Toggle via `control_center`:

```python
{ "action": "set", "key": "workflows_enabled", "value": true }
```

## Related documents

- [docs/profiles.md](./profiles.md) — Profile workflow directory
- [docs/team.md](./team.md) — Teams as an alternative coordination primitive
- [docs/acp.md](./acp.md) — ACP agents as external workflow executors

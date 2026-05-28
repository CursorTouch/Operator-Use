# Cron

The cron system lets the agent schedule recurring or one-shot tasks that fire on a fixed interval or a cron expression. When a job fires, its `message` is injected into the agent as a prompt — identical to a user typing it — and the full agent loop runs normally. Jobs persist across restarts in a JSON file.

## Architecture

```
RuntimeContext.create()
  └── Cron(store_path)               # scheduler, loads crons.json
        └── injected into CronTool  # builtin tool the LLM uses to manage jobs

Runtime.__init__()
  └── cron.on_job = _handle_cron_job
  └── cron.start()                   # asyncio background loop begins

cron._loop()
  └── sleeps until next due job
  └── _tick() → asyncio.create_task(_run_job(job))
        └── on_job(job) → runtime.invoke(job.payload.message, source='cron')
```

The `Cron` service and the `cron` tool are created unconditionally in `RuntimeContext.create()` when cron is enabled. The callback (`on_job`) is wired by `Runtime.__init__()` after both the context and the runtime exist, avoiding a chicken-and-egg dependency. `cron.start()` is called immediately after.

## Scheduling modes

### `every` — fixed interval

Fires every N milliseconds, measured from the previous run time. The first run fires N ms after the job is created.

```json
{ "mode": "every", "interval_ms": 3600000 }
```

### `cron` — cron expression

Fires according to a 5-field cron expression evaluated in a given IANA timezone. Uses `croniter` to compute the next scheduled time.

```json
{ "mode": "cron", "expr": "0 9 * * 1-5", "tz": "America/New_York" }
```

Standard 5-field format: `minute hour dom month dow`.

## Persistence

Jobs are stored in `~/.operator/profiles/<name>/crons.json` (global, not project-scoped). The file is written synchronously on every mutation (add, update, remove, enable/disable, mark-run). On startup, `Cron._load()` reads the file lazily — only on the first access — so the store is re-read from disk if the process restarts.

```json
{
  "version": 1,
  "jobs": [
    {
      "id": "uuid",
      "name": "morning-brief",
      "enabled": true,
      "schedule": { "mode": "cron", "expr": "0 9 * * *", "tz": "UTC" },
      "payload": { "message": "Give me a morning brief." },
      "state": {
        "next_run_at_ms": 1748000000000,
        "last_run_at_ms": null,
        "last_status": null,
        "last_error": null
      },
      "created_at_ms": 1747900000000,
      "updated_at_ms": 1747900000000,
      "delete_after_run": false
    }
  ]
}
```

## Job lifecycle

```
add_job() → CronJob (enabled=True, next_run_at_ms computed)
  │
  └── _loop() sleeps until next_run_at_ms
        └── _tick() fires all due jobs
              └── _run_job(job)
                    ├── on_job(job) → agent processes message
                    ├── _mark_run(job, "success")  ← updates next_run_at_ms
                    └── remove_job(job.id)          ← only if delete_after_run=True
```

If `on_job` raises, the job is marked `"failure"` with the error string, and `next_run_at_ms` is still advanced so it retries on the next scheduled tick.

## The `cron` tool

The LLM manages jobs via the builtin `cron` tool. It is a single unified tool with an `action` parameter.

### `list`

Returns all jobs with their current state.

```
cron(action="list")
```

### `add`

Creates a new job. Both `name` and `message` are required.

```
cron(
  action="add",
  name="daily-summary",
  schedule_mode="cron",
  expr="0 18 * * *",
  tz="Asia/Kolkata",
  message="Summarize what I accomplished today."
)
```

```
cron(
  action="add",
  name="hourly-ping",
  schedule_mode="every",
  interval_ms=3600000,
  message="Check for any pending tasks or reminders."
)
```

Set `delete_after_run=True` for one-shot jobs that fire once and self-remove.

### `update`

Modifies an existing job by `job_id`. Only the fields you supply are changed.

```
cron(action="update", job_id="...", expr="0 8 * * *", tz="UTC")
cron(action="update", job_id="...", message="Updated prompt text.")
```

### `remove`

Permanently deletes a job.

```
cron(action="remove", job_id="...")
```

### `enable` / `disable`

Pause or resume a job without deleting it. A disabled job has `next_run_at_ms = null` and is never ticked.

```
cron(action="disable", job_id="...")
cron(action="enable",  job_id="...")
```

## Tool injection

The `cron` tool is a builtin discovered by `ResourceLoader` from `program/builtins/tools/cron.py`. Because the tool is instantiated at module load time (before `RuntimeContext` exists), the `Cron` service is injected directly onto the tool instance after discovery:

```python
# RuntimeContext.create()
cron = Cron(store_path=get_crons_path(config_dir))
for t in all_tools:
    if t.name == 'cron':
        t._cron = cron
        break
```

This mirrors the `web_fetch` pattern, which stores its LLM reference as `self._llm`. No global singleton is used.

## Settings

### `cron_enabled`

Controls whether the cron scheduler and tool are active. Defaults to `True`.

```json
{ "cron_enabled": false }
```

Set in `~/.operator/settings.json` (global) or `.operator/settings.json` (project). Project scope wins.

When `cron_enabled` is `false`:
- `Cron` is never instantiated and `context.cron` is `None`.
- The `cron` tool is removed from the engine's tool list before the agent starts — the LLM never sees it.
- `Runtime.shutdown()` is a no-op for cron.

Programmatic control:

```python
settings_manager.set_cron_enabled(False)   # disable, persists to global settings
settings_manager.get_cron_enabled()        # → bool (default True)
```

## Types

```python
@dataclass
class CronSchedule:
    mode: Literal['every', 'cron']
    interval_ms: int | None   # mode='every': ms between runs
    expr: str | None          # mode='cron': 5-field cron expression
    tz: str                   # IANA timezone (default 'UTC')

@dataclass
class CronPayload:
    message: str              # prompt injected into the agent

@dataclass
class CronJobState:
    next_run_at_ms: int | None
    last_run_at_ms: int | None
    last_status: Literal['success', 'failure'] | None
    last_error: str | None

@dataclass
class CronJob:
    id: str
    name: str
    enabled: bool
    schedule: CronSchedule
    payload: CronPayload
    state: CronJobState
    created_at_ms: int
    updated_at_ms: int
    delete_after_run: bool
```

## Service API

```python
cron = Cron(store_path=Path("~/.operator/profiles/<name>/crons.json"))
cron.on_job = async_callback          # set before start()
cron.start()                          # begin background loop
cron.stop()                           # cancel background loop

# CRUD
job  = cron.add_job(name, schedule, payload, *, enabled=True, delete_after_run=False)
job  = cron.update_job(job_id, *, name, enabled, schedule, payload)
ok   = cron.remove_job(job_id)        # → bool
job  = cron.enable_job(job_id)        # shorthand for update_job(enabled=True)
job  = cron.disable_job(job_id)       # shorthand for update_job(enabled=False)
jobs = cron.list_jobs()               # → list[CronJob]
job  = cron.get_job(job_id)           # → CronJob | None
```

## Shutdown

`Runtime.shutdown()` calls `cron.stop()`, which cancels the background asyncio task. The main REPL calls `runtime.shutdown()` when the user exits. Pending job runs that are already in-flight as `asyncio.create_task` calls complete normally; only the scheduler loop itself is stopped.

## Related documents

- [agent.md](./agent.md) — `invoke()` and `PromptOptions.source`
- [tool.md](./tool.md) — Tool ABC, discovery, and the builtin tool pattern
- [session.md](./session.md) — How cron-triggered prompts flow through the session

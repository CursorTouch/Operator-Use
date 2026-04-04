# Task Orchestration Quick Reference

## Four Features in One Implementation

### 1. DAG Dependencies

**What it does:** Tasks wait for other tasks to finish.

**LLM code:**
```
Create task A: "Fetch data"
Create task B: "Process data" with depends_on=[task_A]
→ B waits until A completes
```

**Configuration:** No config needed, built-in.

**API:**
```python
task_id = await manager.ainvoke(
    task="...",
    label="...",
    channel="slack",
    chat_id="C123",
    depends_on=[other_task_id],  # ← NEW
)
```

---

### 2. Parallel Execution with TaskPool

**What it does:** Limit how many subagents run at the same time.

**Configuration:**
```json
{
  "agents": {
    "defaults": {
      "subagent": {
        "maxConcurrent": 5
      }
    }
  }
}
```

**What it prevents:**
- 100 simultaneous subagents → only 5 run at once, rest queue
- Resource exhaustion (memory, API rate limits, etc)
- Thundering herd problems

**No code needed** — set config once, applies everywhere.

---

### 3. Retry with Exponential Backoff

**What it does:** Failed tasks automatically retry with increasing delays.

**Configuration:**
```json
{
  "agents": {
    "defaults": {
      "subagent": {
        "maxConcurrent": 5,
        "retry": {
          "maxRetries": 3,           # 3 retries (4 attempts total)
          "baseDelay": 1.0,          # Wait 1s before 1st retry
          "maxDelay": 60.0,          # Never wait more than 60s
          "backoffFactor": 2.0       # Double delay each time
        }
      }
    }
  }
}
```

**Delays:**
- Attempt 1: fails
- Wait 1.0s
- Attempt 2: fails
- Wait 2.0s
- Attempt 3: fails
- Wait 4.0s
- Attempt 4: fails → give up

**No code changes needed** — configure once, applies to all subagents.

---

### 4. Unified Observability Tracing

**What it does:** Emits events for all agent operations.

**One-line setup:**
```python
from operator_use.tracing import Tracer

tracer = Tracer(agent_id="main", on_trace=lambda e: print(e))
agent = Agent(..., tracer=tracer)  # ← NEW param
```

**Captured events:**
- `AGENT_RUN` — Full agent execution
- `LLM_CALL` — LLM invocation (with token counts)
- `TOOL_CALL` — Tool execution (with name, result, success)
- `SUBAGENT_RUN` — Subagent task (with task_id, status)

**Async callback:**
```python
async def on_trace(event):
    # event.event_type: str
    # event.duration_ms: float
    # event.prompt_tokens: int | None
    # event.completion_tokens: int | None
    # event.tool_name: str | None
    # event.subagent_task_id: str | None
    # event.subagent_status: str | None
    log_to_observability_platform(event)

tracer = Tracer(agent_id="main", on_trace=on_trace)
```

---

## Architecture at a Glance

```
User sends message
  ↓
Agent.run(message, tracer=...)
  ├─ TRACE: agent_start
  │
  ├─ LLM call
  │ ├─ TRACE: llm_call (with tokens)
  │
  ├─ Tool call
  │ ├─ TRACE: tool_call
  │
  ├─ Spawn subagents
  │ ├─ subagent A (depends_on=[])
  │   ├─ TRACE: subagent_run
  │   ├─ Retry loop (if config.retry set)
  │   │ ├─ LLM call
  │   │ ├─ TRACE: llm_call (nested under subagent)
  │   │ ├─ On failure: wait, retry
  │   │ └─ TRACE: subagent_end
  │
  │ └─ subagent B (depends_on=[A])
  │   ├─ Wait for A to complete (DAG)
  │   ├─ TRACE: subagent_run
  │   ├─ (same as A)
  │
  └─ TRACE: agent_end
```

---

## Files to Know

| File | Purpose |
|------|---------|
| `operator_use/subagent/views.py` | `SubagentRecord` data |
| `operator_use/subagent/manager.py` | DAG + tracer threading |
| `operator_use/subagent/service.py` | Retry loop + trace emit |
| `operator_use/config/service.py` | `RetryConfig` |
| `operator_use/tracing/service.py` | `Tracer` implementation |
| `ORCHESTRATION_FEATURES.md` | Full documentation |
| `example_orchestration.py` | Example usage |

---

## Examples

### Enable Retry

```json
// config.json
{
  "agents": {
    "defaults": {
      "subagent": {
        "retry": {
          "maxRetries": 3,
          "baseDelay": 1.0,
          "maxDelay": 60.0,
          "backoffFactor": 2.0
        }
      }
    }
  }
}
```

### Use Dependencies

```python
# Agent/tool code (via LLM)
task_a = await manager.ainvoke(
    task="Download data",
    label="Download",
    channel="slack",
    chat_id="C123",
)

task_b = await manager.ainvoke(
    task="Process data",
    label="Process",
    channel="slack",
    chat_id="C123",
    depends_on=[task_a],  # ← B waits for A
)

task_c = await manager.ainvoke(
    task="Generate report",
    label="Report",
    channel="slack",
    chat_id="C123",
    depends_on=[task_b],  # ← C waits for B
)
```

### Add Tracing

```python
from operator_use.tracing import Tracer

trace_log = []

async def on_trace(event):
    trace_log.append({
        "type": event.event_type,
        "duration": event.duration_ms,
        "tokens": event.total_tokens,
    })

tracer = Tracer(agent_id="main", on_trace=on_trace)
agent = Agent(..., tracer=tracer)
```

---

## Backward Compatibility

All features are **opt-in**:
- Don't set `retry` → no retry (original behavior)
- Don't use `depends_on` → no dependency (original behavior)
- Don't pass `tracer` → zero overhead (original behavior)

**Nothing breaks, everything still works.**

---

## Testing

```bash
# Run all tests
python -m pytest test_orchestration_features.py -v

# Result: 8 passed ✓
```

---

## When to Use Each Feature

| Feature | When |
|---------|------|
| **Dependencies** | Multi-stage workflows (fetch → process → analyze → report) |
| **TaskPool** | Limiting resource usage, preventing API rate limit hammering |
| **Retry** | Flaky APIs, network timeouts, transient failures |
| **Tracing** | Monitoring, cost analysis, performance profiling |

Use all four together for robust, observable, resource-efficient pipelines.

---

## Support

1. **Read** `ORCHESTRATION_FEATURES.md` for full documentation
2. **Check** `example_orchestration.py` for working code
3. **Run** `test_orchestration_features.py` to verify
4. **See** original plan: `validated-noodling-brooks.md`

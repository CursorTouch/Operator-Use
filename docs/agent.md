# Agent lifecycle

`Agent` is the orchestration layer above the low-level `Engine`. It owns session persistence, retry logic, extension event dispatch, compaction scheduling, and the interface through which extensions interact with the running session.

This document describes the current behavior and the design intent behind the wiring decisions.

## Layers

```
invoke(user_input)
  └─ _run_with_retry(ctx, user_entry_id)
        └─ engine.run(ctx)            ← Engine streams LLM events
             │
             ├─ _on_engine_event        ← re-dispatches to ExtensionRuntime
             ├─ _before_tool_call       ← emits 'tool_call', can block
             ├─ _after_tool_call        ← emits 'tool_result', can patch
             └─ _get_ephemeral_messages ← injects live desktop/browser state per turn
```

## Phase model

Agent tracks an explicit phase string:

```python
self._phase: str   # "idle" | "turn" | "compaction"
```

Structural operations require `phase == "idle"`:

- `invoke()`
- `run_compaction()`

Both set the phase synchronously before the first `await` and restore it in a `finally` block.

Calling either while the agent is busy raises `RuntimeError` immediately.

## Turn flow

`invoke()` follows this sequence:

```
1. Assert phase == "idle"
   │
2. Emit 'input' event
   ├─ Extensions see raw user text
   │
3. Rebuild system prompt
   ├─ Load skills, knowledge, context files
   ├─ Apply AGENT.md + SYSTEM.md
   │
4. Emit 'before_agent_start'
   ├─ Extensions may replace system prompt
   │
5. Reconstruct message history
   ├─ Load session tree from disk
   ├─ Compute leaf-to-root path
   ├─ Apply any compaction summaries
   │
6. Emit 'context'
   ├─ Extensions may replace/filter messages
   │
7. Persist user message
   ├─ Appended once (not per retry)
   │
8. Merge tools
   ├─ Builtin tools override extensions
   │
9. Enter _run_with_retry()
   ├─ Loop: attempt Engine.run()
   ├─      On error: rewind + exponential backoff
   ├─      On success: break
   │
10. After success
   ├─ Emit 'save_point'
   ├─ Persist AssistantMessage to disk
   ├─ Emit 'agent_end'
   │
11. Check compaction
   ├─ If context_tokens > window - reserve_tokens
   ├─    Run compaction, emit 'after_compaction'
   │
12. Check for queued follow-ups
   └─ If none: emit 'settled'
```

**Key invariants:**
- Session writes are deferred until `AssistantMessage` (turn succeeded)
- Failed retries fully rewind before next attempt
- User message is persisted once before retry loop (can be removed if all retries fail)
- Compaction only runs after `save_point` (turn complete, session durable)

## Retry

`_run_with_retry()` implements exponential-backoff retry on Engine errors.

```python
for attempt in range(max_retries + 1):
    unsubscribe = self._register_message_handler(persisted_ids)
    try:
        await self._engine.run(ctx)
    finally:
        unsubscribe()

    error = self._engine.state.error_message
    if error is None:
        return  # success

    self._rewind_session(persisted_ids)
    self._engine.reset()
    await asyncio.sleep(base_delay_s * 2 ** (attempt - 1))
```

**Example walkthrough:**

Session state (before invoke):
```
leaf_id = "msg-2"
[msg-0] User: "fix the auth bug"
[msg-1] Assistant: "I'll help..."
[msg-2] Tool result: {...}
```

User input: "continue working"

**Attempt 0:**
- Persist user msg-3: `[..., msg-3-user]`
- Engine runs, LLM returns assistant msg (pending)
- Engine fails with timeout
- Rewind: remove unpersisted messages (LLM response)
- Reset Engine, wait 2s

**Attempt 1:**
- Engine runs again, succeeds
- Persist assistant msg-4 to disk
- Return to invoke()

**Final session on disk:**
```
[msg-0] User: "fix the auth bug"
[msg-1] Assistant: "I'll help..."
[msg-2] Tool result: {...}
[msg-3] User: "continue working"
[msg-4] Assistant: "resuming..."
```

If all retries exhaust, the user message (msg-3) is also rewound and removed.

**Configuration:**
- Disable: `config.retry_enabled = False`
- Max attempts: `config.retry_config.max_retries = 5` (default: 3)
- Delay schedule: `base_delay_ms * 2^(attempt-1)` — pure exponential backoff, no jitter

## Session persistence

Messages are persisted in the `_register_message_handler` hook, registered once per attempt:

- `AssistantMessage`: persisted on `message_end`; token usage is extracted and `_context_tokens` is updated.
- `ToolMessage`: persisted on `message_end`.

The handler is unregistered in a `finally` block so it is not called after the attempt ends, regardless of success or error. `persisted_ids` accumulates entry IDs for rollback.

## Extension event wiring

Engine fires low-level events through `options.on_event`. Agent intercepts every event and re-dispatches it to each loaded extension's handlers via `_on_engine_event()`:

```python
async def _on_engine_event(self, event):
    event_type = getattr(event, 'type', None)
    for ext in self._extensions._extensions:
        for handler in ext.handlers.get(event_type, []):
            await handler(event, self)
```

Extension errors are caught and appended to `self._extensions._errors`. They do not abort the active turn.

## Tool hooks

Agent sets four callbacks on `Engine.options` at construction time:

- `options.before_tool_call = self._before_tool_call`
- `options.after_tool_call = self._after_tool_call`
- `options.on_event = self._on_engine_event`
- `options.get_ephemeral_messages = self._get_ephemeral_messages`

**before_tool_call**: Emits `tool_call` to extensions. If any handler returns `ToolCallEventResult(block=True)`, the call is short-circuited and a `ToolResultContent(is_error=True)` is returned to the Engine. The LLM sees this as a tool error.

**after_tool_call**: Emits `tool_result` to extensions. Handlers may return `ToolResultEventResult` to patch `content`, `is_error`, or set `terminate=True`. Patches accumulate — each handler sees the previous patched state. Setting `terminate=True` signals the Engine to skip the follow-up LLM call if every tool in the batch terminates.

**get_ephemeral_messages**: Called by the Engine at the start of each turn before the LLM call. Agent inspects `tool_context.desktop` and `tool_context.browser` — if either is open, `get_state()` is called and the result is wrapped in a `UserMessage` (with an image if `use_screenshot=True`). These messages are appended to `ctx_messages` for that LLM call only and are never written to session history. Controlled by `ComputerUseSettings` and `BrowserUseSettings` baked into the desktop/browser instances at runtime startup:

| Flag | Effect |
|---|---|
| `use_screenshot` | Screenshot is captured and embedded as an image in the message |
| `use_accessibility` | DOM/accessibility tree is captured and included as text |
| (always) | Basic app/window/tab info is always present when the tool is open |

## System prompt reconstruction

`_rebuild_system_prompt()` assembles the system prompt in this order:

1. **Identity** — `SYSTEM.md` (replaces everything) → `SOUL.md` (replaces default persona) → `"You are a helpful assistant."` + guidelines.
2. **Docs reference** — path to the `docs/` directory with a per-topic file map; injected only if the directory exists. Agent reads lazily when asked about Operator internals.
3. **Append section** — `append_system_prompt` content (includes knowledge injection — see [knowledge.md](./knowledge.md)).
4. **Memory** — contents of `MEMORY.md` if present.
5. **User profile** — contents of `USER.md` if present.
6. **Skills** — `<available_skills>` XML block with enforcement rules; only when `read` or `skill` tool is available. Each entry includes `<location>` with the absolute path to `SKILL.md`.
7. **Platform hint** — channel-specific formatting rules (Telegram, Discord, Slack, etc.).
8. **Footer** — always appended last:
   ```
   Current date: <ISO date>
   Current working directory: <cwd>
   Global directory: ~/.operator
   ```
   When a profile is active, the footer expands to include:
   ```
   Profile directory: ~/.operator/profiles/<name>
   Profile SOUL.md:   ~/.operator/profiles/<name>/SOUL.md    ← persona and identity
   Profile MEMORY.md: ~/.operator/profiles/<name>/MEMORY.md  ← long-term memory
   Profile USER.md:   ~/.operator/profiles/<name>/USER.md    ← who you are talking to
   Profile skills directory: ~/.operator/profiles/<name>/skills
   Profile knowledge directory: ~/.operator/profiles/<name>/knowledge
   Profile temp directory: ~/.operator/profiles/<name>/temp  ← scratchpad / terminal CWD
   Session ID: <id>   ← only when a session exists
   ```

When a custom prompt (`SYSTEM.md`) is set it replaces the identity layer only — docs, memory, user, skills, platform, and footer are still appended.

After `before_agent_start`, extensions may replace the entire system prompt string.

## Compaction scheduling

Compaction runs after `agent_end` (the turn is complete and session writes are durable):

```python
if self._compact_requested or self._compaction.should_compact(
    self._context_tokens, self._context_window
):
    await self._run_compaction(opts.compaction_custom_instructions)
```

Extensions may call `ctx.compact()` during a turn to set `_compact_requested`. The actual compaction always runs after the turn, never mid-turn.

`run_compaction()` is also a public API for manual compaction. It checks `phase == "idle"` and fails fast if called while a turn is running. After a successful manual compaction it emits `save_point` and optionally `settled`.

See [compaction.md](./compaction.md) for the Compaction implementation.

## Session navigation

Agent exposes `new_session()`, `fork()`, and `switch_session()` — all forwarded to the `Runtime` if one is wired up. These are structural operations that require the agent to be idle. They do not go through the retry machinery.

## ExtensionContext interface

`Agent` implements `ExtensionContext`, which is the object passed to every extension event handler. Extensions see:

- `cwd` — the working directory
- `session_manager` — the live `SessionManager`
- `model` / `model_registry` — current model and the full registry
- `signal` — the Engine's abort signal
- `is_idle()` / `has_pending_messages()`
- `abort()` / `shutdown()`
- `get_context_usage()` — token count relative to context window
- `get_system_prompt()` — the most recently built system prompt
- `compact()` — request compaction after the current turn
- `run_compaction()` — run compaction now (must be idle)
- `reload()` — reload resource files
- `wait_for_idle()` — await Engine settlement
- `new_session()` / `fork()` / `switch_session()` — session navigation

## Events emitted by Agent (not Engine)

| Event | When |
|---|---|
| `input` | Before processing starts — raw user text |
| `before_agent_start` | After system prompt is built, before Engine run |
| `context` | After session is loaded, before AgentContext snapshot |
| `agent_end` | After all Engine turns complete and session is written |
| `save_point` | After `agent_end` — session writes are durable |
| `settled` | After `save_point` when no queued follow-up turns remain |
| `session_before_compact` | Before compaction runs — can cancel or replace |
| `session_compact` | After compaction entry is appended |
| `retry_start` | Before each retry attempt (delay not yet applied) |
| `retry_end` | After each attempt completes, with success flag |

Engine events (`agent_start`, `turn_start`, `message_end`, `tool_execution_*`, …) are re-dispatched through `_on_engine_event`. See [engine.md](./engine.md) for the full Engine event list.

## Related documents

- [engine.md](./engine.md) — Engine loop and tool execution
- [session.md](./session.md) — Session persistence, tree navigation
- [hooks.md](./hooks.md) — Hooks system event types and result semantics
- [extensions.md](./extensions.md) — Extension loading and dispatch
- [compaction.md](./compaction.md) — Compaction logic

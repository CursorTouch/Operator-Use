# Architecture — Full System Flow

Low-level block diagram of every layer from inbound channel message to LLM call,
tool execution, session persistence, background review, and outgoing response.

---

## Overview

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                              OPERATOR — FULL FLOW                                   │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

Four layers build on each other — each layer knows nothing above it:

```
Engine  ←  Agent  ←  Runtime  ←  Gateway
```

---

## Entry Points — Channels

```
 ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
 │ Telegram │  │ Discord  │  │  Slack   │  │WebSocket │  │  stdio   │  │  Email   │
 │ (PTB)    │  │(discord  │  │(bolt+    │  │  Server  │  │ (REPL)   │  │  (IMAP/  │
 │ polling  │  │  .py)    │  │ socket)  │  │          │  │          │  │  SMTP)   │
 └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
      │             │             │             │             │             │
      └─────────────┴─────────────┴─────────────┬─────────────┴─────────────┘
                                                │  BaseChannel.receive(IncomingMessage)
                                                ▼
```

Every channel subclasses `BaseChannel` and calls `self.bus.publish_incoming(msg)`.
`IncomingMessage` carries `channel`, `chat_id`, `parts` (text/audio/image/file), and
optional `user_id` / `message_id` for threading and reactions.

---

## Gateway Layer

```
                               ┌──────────────────────┐
                               │      GatewayManager  │
                               │ ┌──────────────────┐ │
                               │ │       Bus        │ │
                               │ │ incoming_queue   │ │
                               │ │ outgoing_queue   │ │
                               │ └────────┬─────────┘ │
                               │          │           │
                               │ ┌────────▼─────────┐ │
                               │ │     Gateway      │ │
                               │ │                  │ │
                               │ │ _incoming_loop   │ │◄── hook: message:receive
                               │ ├ lookup/create    │ │    (STT transform, reject)
                               │ │  │  Agent by     │ │
                               │ │  │  channel:     │ │
                               │ │  │  chat_id      │ │
                               │ │  └─► spawn Task  │ │
                               │ │                  │ │
                               │ │ _outgoing_loop   │ │──► BaseChannel.send()
                               │ │  └ route to      │ │    (stream phases: START/
                               │ │    channel.send  │ │     CHUNK/END/DONE/ERROR)
                               │ └──────────────────┘ │
                               └──────────────────────┘
                                 ▲ hook: message:send
                                   (TTS inject audio)
```

**`message:receive` hook** — fires before the agent sees input. The builtin STT hook
detects `AudioPart`, transcribes it, and transforms to `TextPart`. Handlers may also
`reject` the message entirely (e.g. rate-limiting extensions).

**`message:send` hook** — fires after the agent turn completes, before the `DONE`
frame. The builtin TTS hook synthesizes speech and injects an `AudioPart` reply.

**Session model** — each `channel:chat_id` pair gets its own `Agent` instance:

```
"telegram:987654321"          → Agent (Telegram DM)
"slack:C123456:1680000000.0"  → Agent (Slack thread)
"ws:4398046511104"            → Agent (WebSocket connection)
```

If a session is already processing when the next message arrives the new text is
steered into the running agent via `engine.steer()` — not queued as a new turn.

**Stream phases** sent to channels:

| Phase | Meaning |
|---|---|
| `START` | Turn beginning — start typing indicator |
| `CHUNK` | Streaming content: `kind` = text / thinking / tool_start / tool_update / tool_end |
| `END` | Assistant message complete — flush buffered text |
| `DONE` | Full turn complete (after TTS) |
| `ERROR` | Turn ended with error |

---

## Runtime Layer

```
                          ┌──────────────────────────────┐
                          │           Runtime            │
                          │                              │
                          │  ┌──────────┐ ┌───────────┐  │
                          │  │  Slash   │ │   Cron    │  │
                          │  │ Commands │ │ Scheduler │  │
                          │  └──────────┘ └───────────┘  │
                          │  ┌──────────┐ ┌───────────┐  │
                          │  │ Subagent │ │  Session  │  │
                          │  │ Wiring   │ │ Lifecycle │  │
                          │  └──────────┘ └───────────┘  │
                          └──────────────────────────────┘
                                          │
                                          ▼  agent.invoke(user_input)
```

Runtime owns the outer session lifecycle (new session, fork, switch), wires up the
GatewayManager and CronScheduler, and dispatches slash commands before they reach
the Agent.

---

## Agent Layer — Turn Flow

```
       ┌───────────────────────────────────────────────────────────────┐
       │                         Agent.invoke()                        │
       │                                                               │
       │  1. assert phase == IDLE                                      │
       │  2. guardrail.on_turn_start() ◄── reset per-turn loop state   │
       │  3. emit 'input' ──────────────────────────────► extensions   │
       │  4. start memory prefetch (background asyncio Task)           │
       │  5. rebuild system prompt (or hit per-channel cache)          │
       │     ┌────────────────────────────────────────┐                │
       │     │ SystemPromptBuilder                    │                │
       │     │  SYSTEM.md / SOUL.md / default persona │                │
       │     │  + docs reference                      │                │
       │     │  + knowledge injection                 │                │
       │     │  + MEMORY.md contents                  │                │
       │     │  + USER.md contents                    │                │
       │     │  + <available_skills> XML              │                │
       │     │  + platform hint (Telegram/Discord/…)  │                │
       │     │  + footer (date, cwd, session id)      │                │
       │     └────────────────────────────────────────┘                │
       │  6. emit 'before_agent_start' ──────────────► extensions      │
       │     (extensions may replace system prompt)                    │
       │  7. await memory prefetch → memory_context                    │
       │  8. reconstruct session context                               │
       │     ┌──────────────────────────────────────┐                  │
       │     │ SessionManager.build_session_context │                  │
       │     │  walk leaf→root path                 │                  │
       │     │  apply CompactionEntry if present    │                  │
       │     │  return ordered message list         │                  │
       │     └──────────────────────────────────────┘                  │
       │  9. emit 'context' ──────────────────────────► extensions     │
       │     (extensions may replace/filter messages)                  │
       │ 10. persist UserMessage to JSONL (once, before retry loop)    │
       │ 11. merge tools (builtins override extensions)                │
       │ 12. ─────────────────────────────────────────────────────►    │
       │           _run_with_retry()                                   │
       └───────────────────────────────────────────────────────────────┘
```

### Retry loop

```
       ┌─────────────────────────────────────────┐
       │           _run_with_retry()             │
       │                                         │
       │  for attempt in 0..max_retries:         │
       │    register message handler             │
       │    ──────────────────────────────────►  │
       │         engine.run(AgentContext)        │
       │    ◄──────────────────────────────────  │
       │    if error:                            │
       │      classify_error(exc) → ErrorKind    │
       │      if not retryable → abort           │
       │      if CONTEXT_OVERFLOW → compact first│
       │      rewind session (remove unpersisted)│
       │      engine.reset()                     │
       │      sleep(base * 2^attempt) ── backoff │
       │      emit 'retry_start' / 'retry_end'   │
       │    else: break                          │
       └─────────────────────────────────────────┘
```

Session writes are deferred — only committed after `AssistantMessage` arrives.
Failed retries are fully rewound before the next attempt. The user message is
persisted once before the loop; if all retries exhaust it is also removed.

### Post-turn actions

```
       ┌───────────────────────────────────────────────────────────────┐
       │              After Engine Completes (post-turn)               │
       │                                                               │
       │  emit 'save_point' ──────────────────────► extensions         │
       │  persist AssistantMessage to JSONL                            │
       │  emit 'agent_end' ───────────────────────► extensions         │
       │                                                               │
       │  ┌──────────────────────────────────────────────────────┐     │
       │  │  Background Reviewers (daemon threads)               │     │
       │  │                                                      │     │
       │  │  if skill_review.should_review() (every 10 calls):   │     │
       │  │    spawn_skill_review()                              │     │
       │  │    └─ Thread: skill-review                           │     │
       │  │       └─ child = agent.spawn_child(tools=['skill'])  │     │
       │  │          ├─ same system prompt (cache hit)           │     │
       │  │          ├─ same full tool list in request body      │     │
       │  │          ├─ whitelist: skill only at dispatch        │     │
       │  │          └─ engine.run(review_task) timeout=120s     │     │
       │  │                                                      │     │
       │  │  if memory_review.should_review() (every 10 calls):  │     │
       │  │    spawn_memory_review()                             │     │
       │  │    └─ Thread: memory-review                          │     │
       │  │       └─ child = agent.spawn_child(tools=['memory']) │     │
       │  │          ├─ same system prompt (cache hit)           │     │
       │  │          ├─ on_complete → clear system_prompt_cache  │     │
       │  │          └─ engine.run(review_task) timeout=60s      │     │
       │  └──────────────────────────────────────────────────────┘     │
       │                                                               │
       │  if compact_requested or tokens > window - reserve:           │
       │    run_compaction()                                           │
       │                                                               │
       │  if no queued turns: emit 'settled'                           │
       └───────────────────────────────────────────────────────────────┘
```

**Background reviewers** fork a child agent using `spawn_child(tools=[...])`. The
child inherits the parent's system prompt byte-for-byte and sends the full parent
tool list in the request body — so the provider prefix cache is reused and only the
short review task is uncached. The tool whitelist is enforced at dispatch time, not
in the request, so the cache key stays identical to the parent turn.

---

## Engine Layer — LLM Loop

```
       ┌──────────────────────────────────────────────────────────────┐
       │                    Engine.run(AgentContext)                  │
       │                                                              │
       │  emit agent_start                                            │
       │                                                              │
       │  ┌────────────── TURN LOOP ─────────────────────────────┐    │
       │  │                                                      │    │
       │  │  emit turn_start                                     │    │
       │  │  emit message_start                                  │    │
       │  │                                                      │    │
       │  │  ┌────── LLM STREAMING ──────────────────────────┐   │    │
       │  │  │  llm.stream(LLMContext)                       │   │    │
       │  │  │                                               │   │    │
       │  │  │  TextDeltaEvent  → emit message_update (text) │   │    │
       │  │  │  ThinkingDelta   → emit message_update (think)│   │    │
       │  │  │  ToolCallEnd     → collect tool call          │   │    │
       │  │  │  ErrorEvent      → set stop_reason=Error      │   │    │
       │  │  │  EndEvent        → set stop_reason + tokens   │   │    │
       │  │  └───────────────────────────────────────────────┘   │    │
       │  │                                                      │    │
       │  │  emit message_end                                    │    │
       │  │                                                      │    │
       │  │  ┌── stop_reason ─────────────────────────────────┐  │    │
       │  │  │                                                │  │    │
       │  │  │  Error / Abort ──► emit agent_error, turn_end  │  │    │
       │  │  │                    break                       │  │    │
       │  │  │                                                │  │    │
       │  │  │  Stop ──────────► drain follow_up_queue        │  │    │
       │  │  │                   if empty: emit turn_end,     │  │    │
       │  │  │                   break                        │  │    │
       │  │  │                                                │  │    │
       │  │  │  ToolCalls ─────► _execute_tool_calls()        │  │    │
       │  │  │                   drain steering_queue         │  │    │
       │  │  │                   loop again                   │  │    │
       │  │  └────────────────────────────────────────────────┘  │    │
       │  └──────────────────────────────────────────────────────┘    │
       │                                                              │
       │  emit agent_end                                              │
       └──────────────────────────────────────────────────────────────┘
```

Engine has no knowledge of sessions, extensions, or compaction. All of those are
Agent's responsibility. Engine only fires events through `options.on_event`; Agent
intercepts every event and re-dispatches to extensions via `_on_engine_event()`.

**Steering queue** — drained after tool results, before the next LLM call. Used to
inject mid-flight pivots from the gateway (concurrent incoming message) or extensions.

**Follow-up queue** — drained only when the LLM stops without tool calls. Used to
queue a follow-up prompt that fires immediately after the current response finishes.

---

## Tool Execution

```
       ┌─────────────────────────────────────────────────────────────┐
       │                  _execute_tool_calls()                      │
       │                                                             │
       │  Mode: Sequential | Parallel | Batch (default)              │
       │  (individual tools may override their own execution mode)   │
       │                                                             │
       │  For each tool call:                                        │
       │                                                             │
       │  1. resolve tool by name (builtin wins over extension)      │
       │                                                             │
       │  2. TOOL WHITELIST (child agents only, dispatch-time)       │
       │     if _tool_whitelist set and name not in it:              │
       │       → return error result (full tool list in body stays   │
       │         byte-identical → provider cache hit preserved)      │
       │                                                             │
       │  3. BEFORE HOOKS                                            │
       │     ┌───────────────────────────────────────────────┐       │
       │     │  Agent._before_tool_call()                    │       │
       │     │   emit 'tool_call' → extensions               │       │
       │     │   if any handler blocks → return error result │       │
       │     └───────────────────────────────────────────────┘       │
       │     ┌───────────────────────────────────────────────┐       │
       │     │  Guardrails.before_call() (all, in order)     │       │
       │     │   allow / warn → continue to execution        │       │
       │     │   block → return synthetic error result       │       │
       │     │   halt  → engine.abort(), return error        │       │
       │     └───────────────────────────────────────────────┘       │
       │                                                             │
       │  4. emit tool_execution_start                               │
       │                                                             │
       │  5. tool.execute(id, params, signal, on_update)             │
       │     ┌──────────────────────────────────────────────────┐    │
       │     │  BUILTIN TOOLS (selection):                      │    │
       │     │  read, edit, write, terminal, browser, computer  │    │
       │     │  web_search, web_fetch, memory, skill            │    │
       │     │  subagent, team, peer_agent, send, cron          │    │
       │     │  todo, knowledge, workflow, sandbox, …           │    │
       │     └──────────────────────────────────────────────────┘    │
       │                                                             │
       │  6. emit tool_execution_update (streaming progress)         │
       │  7. emit tool_execution_end                                 │
       │                                                             │
       │  8. AFTER HOOKS                                             │
       │     ┌───────────────────────────────────────────────┐       │
       │     │  Agent._after_tool_call()                     │       │
       │     │   emit 'tool_result' → extensions             │       │
       │     │   handlers may patch content / is_error       │       │
       │     │   handlers may set terminate=True             │       │
       │     └───────────────────────────────────────────────┘       │
       │     ┌───────────────────────────────────────────────┐       │
       │     │  Guardrails.after_call() (all, in order)      │       │
       │     │   warn  → append reason to result content     │       │
       │     │   halt  → engine.abort()                      │       │
       │     └───────────────────────────────────────────────┘       │
       │                                                             │
       │  9. increment SkillReviewTracker + MemoryReviewTracker      │
       └─────────────────────────────────────────────────────────────┘
```

If every result in a batch has `terminate=True` the loop exits after `turn_end`
without making another LLM call.

---

## Inference Layer

```
       ┌─────────────────────────────────────────────────────────────┐
       │                         LLM / Providers                     │
       │                                                             │
       │  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐    │
       │  │  Anthropic   │  │   OpenAI     │  │  OpenRouter /   │    │
       │  │  (claude-*)  │  │  (gpt-*/o*)  │  │  Ollama / etc.  │    │
       │  └──────────────┘  └──────────────┘  └─────────────────┘    │
       │                                                             │
       │  Prefix caching:                                            │
       │   • system prompt + full tool list cached after first turn  │
       │   • child (background review) sends byte-identical prefix   │
       │     → cache hit; only the review task message is uncached   │
       └─────────────────────────────────────────────────────────────┘
```

---

## Session Persistence

```
       ┌─────────────────────────────────────────────────────────────┐
       │                       SessionManager                        │
       │                                                             │
       │  JSONL file: ~/.operator/profiles/<name>/sessions/*.jsonl   │
       │                                                             │
       │  Line 1:  SessionHeader (session_id, cwd, timestamp)        │
       │  Line N:  SessionEntry  (id, parent_id, timestamp, payload) │
       │                                                             │
       │  Entry types:                                               │
       │   MessageEntry       → one AgentMessage(user/assistant/tool)│
       │   CompactionEntry    → summary + first_kept_entry_id        │
       │   LeafEntry          → durable navigation pointer           │
       │   BranchEntry        → branch-point with old-branch summary │
       │   ModelChangeEntry   → records model switch                 │
       │   ThinkingLevelChange→ records thinking-level switch        │
       │                                                             │
       │  leaf_id ──► current tip of active branch                   │
       │                                                             │
       │  build_session_context():                                   │
       │    walk leaf→root, reverse                                  │
       │    if CompactionEntry: prepend summary, keep tail + after   │
       │    return ordered message list for LLM                      │
       │                                                             │
       │  Lazy write: no file until first AssistantMessage           │
       │  Append-only after first flush — O(1) per entry             │
       │  Rewind: remove unpersisted entries on retry failure        │
       └─────────────────────────────────────────────────────────────┘
```

---

## Extension / Hook Bus

All Engine events funnel through `Agent._on_engine_event()` then fan out to every
loaded extension. Extension errors are caught and logged — they never abort a turn.

```
  Engine event
      │
      ▼
  Agent._on_engine_event(event)
      │
      ├──► Extension 1 handlers
      ├──► Extension 2 handlers
      └──► Extension N handlers
```

**Events emitted by Agent** (not Engine):

| Event | When |
|---|---|
| `input` | Before processing — raw user text |
| `before_agent_start` | After system prompt built, before Engine run |
| `context` | After session loaded, before AgentContext snapshot |
| `agent_end` | After Engine completes and session is written |
| `save_point` | After `agent_end` — session writes are durable |
| `settled` | After `save_point` with no queued follow-ups |
| `retry_start` / `retry_end` | Around each retry attempt |
| `session_before_compact` | Before compaction — can cancel or replace |
| `session_compact` | After compaction entry appended |

Gateway hook events (`message:receive`, `message:send`) fire in the Gateway loop,
before and after Agent is involved.

---

## Memory System

```
  invoke() start
      │
      ├── prefetch Task (background asyncio)
      │     └─ MemoryManager.prefetch(user_input, session_id)
      │          └─ returns recalled context string
      │
      ├── system prompt includes MEMORY.md + USER.md file contents
      │
      └── memory_context injected into AgentContext messages

  memory tool (foreground): action = remember / recall / forget
  memory review (background thread after every 10 tool calls):
      └─ writes via memory(action="remember")
         on_complete → clear system_prompt_cache
                       (next turn rebuilds with updated MEMORY.md)
```

---

## Related Documents

| Topic | File |
|---|---|
| Turn flow, retry, compaction scheduling | [agent.md](./agent.md) |
| LLM loop, tool execution modes, queues | [engine.md](./engine.md) |
| Session JSONL, branching, reconstruction | [session.md](./session.md) |
| Extension loading, `api.config`, dispatch | [extensions.md](./extensions.md) |
| Event types, hook registration | [hooks.md](./hooks.md) |
| Channels, message bus, stream phases | [gateway.md](./gateway.md) |
| Models, providers, auth | [inference.md](./inference.md) |
| Tool interface, execution modes | [tool.md](./tool.md) |
| Long-term memory, providers | [memory.md](./memory.md) |
| Context compaction | [compaction.md](./compaction.md) |
| Guardrail interface, loading, built-ins | [guardrails.md](./guardrails.md) |

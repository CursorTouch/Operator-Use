# Hooks

`Hooks` is the typed event bus that wires internal agent state changes to extension handlers and application callbacks. It separates the agent core from any observer that wants to react to lifecycle events.

## Core model

Every hook event is a dataclass with a `type` literal field:

```python
@dataclass
class AgentStartEvent:
    type: Literal['agent_start'] = field(default='agent_start', init=False)

@dataclass
class ToolCallEvent:
    type: Literal['tool_call'] = field(default='tool_call', init=False)
    tool_call_id: str = ''
    tool_name: str = ''
    input: dict[str, Any] = field(default_factory=dict)
```

Result types are separate dataclasses returned by handlers that want to influence behaviour:

```python
@dataclass
class ToolCallEventResult:
    block: bool = False
    reason: str | None = None
```

The `HookEvent` union in `program/hooks/types.py` is the complete set of all events.

## Registration

Three registration forms:

```python
# Imperative
unsubscribe = hooks.register('tool_call', my_handler)
unsubscribe()  # remove later

# Decorator
@hooks.on('agent_end')
async def on_end(event: AgentEndEvent) -> None:
    print(event.messages)

# Catch-all observer (receives every event, return value ignored)
unsub = hooks.subscribe(lambda event: print(event.type))
```

All forms return an unsubscribe callable. The `register()` form is preferred when the handler needs to be removed dynamically.

## Emit semantics

`hooks.emit(event)` calls every handler registered for `event.type`, followed by all catch-all subscribers:

```python
results: list[Any] = []
for handler in handlers[event.type]:
    result = await handler(event)
    results.append(result)

for subscriber in subscribers:
    await subscriber(event)   # return value discarded

return results
```

Exceptions in handlers are logged and suppressed — a failing handler never aborts the pipeline. Subscribers never contribute to the results list.

## Mutation semantics

Hooks are observational by default. Some events carry result types that the caller inspects to mutate behaviour.

### tool_call — block a tool call

Handler returns `ToolCallEventResult(block=True, reason="...")` to prevent execution. The Engine converts this to a `ToolResultContent(is_error=True)` that the LLM sees as a tool error.

### tool_result — patch a tool result

Handler returns `ToolResultEventResult` with any of `content`, `is_error`, or `terminate=True`. Patches accumulate in source order — each handler sees the previous patched result.

### context — replace the message list

Handler returns `ContextEventResult(messages=[...])` to replace the messages passed to the LLM for this turn. Multiple handlers chain: each sees the list returned by the previous.

### before_agent_start — override the system prompt

Handler returns `BeforeAgentStartEventResult(system_prompt="...")` to replace the system prompt. Last non-None result wins.

### session_before_compact — cancel or replace compaction

Handler returns `SessionBeforeCompactResult(cancel=True)` to skip compaction entirely, or `SessionBeforeCompactResult(compaction=result)` to supply a pre-computed `CompactionResult` instead of calling the LLM.

### session_before_switch / session_before_fork — cancel navigation

Handler returns `SessionBeforeSwitchResult(cancel=True)` or `SessionBeforeForkResult(cancel=True)` to veto a session switch or fork.

### message:receive — reject or transform incoming messages

Handler returns `MessageReceiveResult`:

- `action='continue'` — pass through unchanged (default).
- `action='transform'` + `parts=[...]` — replace the message's content parts. The builtin STT hook uses this to convert `AudioPart` to `TextPart`.
- `action='reject'` — drop the message. If `reason` is set, it is sent back to the channel as an error message.

Multiple handlers chain: each result replaces the previous. If any handler rejects, the message is dropped.

### message:send — inject additional output after the agent finishes

Handler returns `MessageSendResult(parts=[...])` to publish extra content to the channel before the `DONE` frame. The builtin TTS hook uses this to return `AudioPart` containing synthesized speech. `event.is_voice` is `True` when the original incoming message contained an `AudioPart`, letting TTS hooks gate synthesis on whether the user spoke vs. typed.

### session_before_tree — modify tree navigation

Handler returns `SessionBeforeTreeResult` to cancel the navigation, or supply an alternative summary, custom instructions, or label.

### input — intercept raw user input

Handler returns `InputEventResult` with:

- `action='continue'` — pass through (default)
- `action='transform'` + `text="..."` — replace the input text
- `action='handled'` — the extension handled the input; skip the agent turn entirely

## Event reference

### Session lifecycle

| Event | Payload | When |
|---|---|---|
| `session_start` | `reason`, `previous_session_file` | On startup, reload, new session, resume, or fork |
| `session_before_switch` | `reason`, `target_session_file` | Before switching to a different session file |
| `session_before_fork` | `entry_id`, `position` | Before forking from a specific entry |
| `session_before_compact` | `preparation`, `branch_entries`, `custom_instructions` | Before compaction runs — can cancel or replace |
| `session_compact` | `compaction_entry`, `from_extension` | After compaction entry is appended |
| `session_shutdown` | `reason`, `target_session_file` | Before the session is closed |
| `session_before_tree` | `preparation` | Before tree navigation — can cancel or override summary |
| `session_tree` | `new_leaf_id`, `old_leaf_id`, `summary_entry` | After tree navigation completes |

### Agent lifecycle

| Event | Payload | When |
|---|---|---|
| `input` | `text`, `source` | Raw user input received |
| `before_agent_start` | `prompt`, `system_prompt` | After system prompt is built, before Engine run |
| `context` | `messages` | After session is loaded, before AgentContext snapshot |
| `agent_start` | — | Engine loop begins |
| `agent_end` | `messages` | Engine loop complete |
| `agent_error` | `error` | Turn ended with an error |
| `save_point` | — | Session writes are durable, agent is idle |
| `settled` | — | No more queued follow-up turns |

### Turn and message

| Event | Payload | When |
|---|---|---|
| `turn_start` | `turn_index`, `timestamp` | Each LLM call begins |
| `turn_end` | `turn_index`, `message`, `tool_results` | Each LLM call and its tools complete |
| `message_start` | `message` | Any message begins |
| `message_update` | `message` | Streaming assistant content delta |
| `message_end` | `message` | Any message completes |

### Tool execution

| Event | Payload | When |
|---|---|---|
| `tool_execution_start` | `tool_call` | Tool begins |
| `tool_execution_update` | `partial_tool_result` | Tool streams progress |
| `tool_execution_end` | `tool_result` | Tool finishes |
| `tool_execution_failure` | `tool_name`, `tool_call_id`, `input`, `error` | Tool raised an unexpected exception |
| `tool_call` | `tool_call_id`, `tool_name`, `input` | Before tool executes — can block |
| `tool_result` | `tool_call_id`, `tool_name`, `input`, `content`, `is_error` | After tool executes — can patch |

### Model and thinking

| Event | Payload | When |
|---|---|---|
| `model_select` | `model`, `previous_model`, `source` | Model is changed |
| `thinking_level_select` | `level`, `previous_level` | Thinking level is changed |

### Subagent lifecycle

| Event | Payload | When |
|---|---|---|
| `subagent_start` | `task_id`, `label`, `task` | A subagent task begins |
| `subagent_end` | `task_id`, `label`, `status`, `result` | A subagent task completes (`status`: `completed`, `failed`, `cancelled`) |

### Gateway and channel lifecycle

| Event | Payload | When |
|---|---|---|
| `gateway:startup` | `channel_ids` | After all enabled channels are registered and started |
| `gateway:stop` | `channel_ids` | Before channel tasks are cancelled on shutdown |
| `channel:connect` | `channel_id` | A channel is registered with the gateway |
| `channel:disconnect` | `channel_id` | A channel is unregistered from the gateway |
| `message:receive` | `channel_id`, `chat_id`, `user_id`, `text`, `parts` | Message arrives from a channel, before the agent processes it — can reject or transform |
| `message:send` | `channel_id`, `chat_id`, `input_text`, `response_text`, `is_voice` | Agent turn complete, before DONE frame — can inject audio or other parts |
| `message:cancel` | `channel_id`, `chat_id` | An in-progress session is hard-cancelled |
| `gateway:error` | `channel_id`, `error` | An error occurs while processing a channel message |

### Misc

| Event | Payload | When |
|---|---|---|
| `user_bash` | `command`, `exclude_from_context`, `cwd` | User runs a shell command |
| `resources_discover` | `cwd`, `reason` | Resource reload begins |
| `retry_start` | `attempt`, `max_retries` | Before a retry delay |
| `retry_end` | `attempt`, `success`, `error` | After an attempt completes |

## Error handling

`Hooks.emit()` wraps every handler call in a try/except and logs the traceback. Errors never propagate to the caller. This means:

- A handler that raises cannot block the active turn.
- There is no way for a handler to signal "I failed, abort the pipeline."

If a handler needs to report an error to the user it must use a side channel (e.g., writing to state, calling a UI facade).

## Introspection

```python
hooks.handler_count('tool_call')   # int
hooks.registered_events()          # list[str] — events with at least one handler
hooks.clear('tool_call')           # remove all handlers for one type
hooks.clear()                      # remove all handlers for all types
```

## File-based hook loading

Hooks can be registered without writing an extension. `ResourceLoader` discovers hook files from these directories on `reload()`:

| Directory | Path function | Purpose |
|---|---|---|
| `program/builtins/hooks/` | `get_builtins_hooks_dir()` | Shipped built-in hooks |
| `<project>/.program/agent/hooks/` | `get_hooks_dir(cwd)` | Project-level hooks |
| `~/.program/agent/hooks/` | `get_hooks_dir()` | Global user hooks |

All path functions are defined in `program/settings/paths.py`.

A hook file must export `hooks` — a list of `(event_type, handler)` tuples:

```python
# .program/agent/hooks/logging.py
async def on_agent_end(event):
    print(f"Done: {len(event.messages)} messages")

async def on_tool_execution_end(event):
    print(f"Tool '{event.tool_result.name}' finished")

hooks = [
    ('agent_end', on_agent_end),
    ('tool_execution_end', on_tool_execution_end),
]
```

All discovered `(event_type, handler)` pairs are registered against the `Hooks` instance in `RuntimeContext.create()` before any session starts. The handler signature is `(event) -> Any` — unlike extension handlers, there is no `ctx` argument. Use extensions when you need access to the `Agent` context.

### Builtin hooks

Two builtin hooks ship in `program/builtins/hooks/`:

**`stt.py`** — Speech-to-text. Registered on `message:receive`. Detects `AudioPart` entries, transcribes each with the configured STT model, and returns `MessageReceiveResult(action='transform', parts=[TextPart(transcript)])`. STT settings are read from `settings.stt` (`model`, `provider`, `language`, `enabled`). Returns `None` (pass-through) if no audio parts exist or STT is explicitly disabled.

**`tts.py`** — Text-to-speech. Registered on `message:send`. Synthesizes speech from `event.response_text` using the configured TTS model and returns `MessageSendResult(parts=[AudioPart(...)])`. `is_voice=True` gates synthesis when `enabled` is `None` (default). When `enabled=True`, synthesizes for all messages regardless of input modality. TTS settings are read from `settings.tts` (`model`, `provider`, `voice`, `speed`, `language`, `enabled`).

## Related documents

- [agent.md](./agent.md) — Which events Agent emits and when
- [engine.md](./engine.md) — Which events Engine emits and when
- [extensions.md](./extensions.md) — How ExtensionRuntime wraps Hooks
- [gateway.md](./gateway.md) — Gateway hook integration (STT/TTS pipeline)

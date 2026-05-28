# Engine

`Engine` is the low-level agentic loop. It drives LLM streaming, executes tools, manages steering and follow-up message queues, and emits structured events to its subscriber list.

It has no knowledge of sessions, extensions, or compaction. Those concerns live in [Agent](./agent.md).

## State model

```python
@dataclass
class AgentState:
    system_prompt: str | None
    messages: list[LLMMessage]        # accumulates during the run
    pending_tool_calls: set[str]
    is_streaming: bool
    error_message: str | None
    streaming_message: AssistantMessage | None
    thinking_level: ThinkingLevel | None
    llm: LLM
    tools: list[Tool]
    follow_up_queue: FollowupQueue
    steering_queue: SteeringQueue
```

`messages` grows with each assistant and tool-result message appended during the run. It is the in-memory transcript — session persistence happens in Agent, not Engine.

## Loop

`run(ctx)` accepts an `AgentContext` snapshot (system prompt, messages, tools) and starts the agentic loop. The loop:

1. Emits `agent_start`.
2. Repeats turns until a stop condition is reached.
3. Emits `agent_end` with the final message list.

Each turn:

```
emit(turn_start)
emit(message_start)
stream LLM → collect text/thinking/tool_call events
emit(message_end)

match stop_reason:
  Error | Abort  → emit agent_error, turn_end, break
  ToolCalls      → execute tools → append steering if any → next turn
  Stop           → drain follow-up queue → if none: emit turn_end, break
```

After each turn, `should_stop_after_turn` is checked. If the callback returns `True`, the loop exits before the next provider call.

## LLM streaming

Each turn calls `self.llm.stream(LLMContext(...))` and processes the async event stream:

| LLM event | Engine action |
|---|---|
| `TextDeltaEvent` | Emit `message_update` with partial text |
| `ThinkingDeltaEvent` | Emit `message_update` with partial thinking |
| `TextEndEvent` | Append completed text to message |
| `ThinkingEndEvent` | Append completed thinking to message |
| `ToolCallEndEvent` | Collect tool call, append to message contents |
| `ErrorEvent` | Set `message.stop_reason` and `message.error` |
| `EndEvent` | Set stop reason and token usage on message |

The `AssistantMessage` is built incrementally. `message_end` is emitted once after the stream closes with the final assembled message.

## Tool execution

After a `ToolCalls` stop reason, Engine resolves tool calls through `_execute_tool_calls()`. Three modes:

| Mode | Behaviour |
|---|---|
| `Sequential` | One tool at a time, in assistant source order |
| `Parallel` | All tools launched concurrently via `asyncio.gather` |
| `Batch` | Per-tool mode: tools with `execution_mode=Parallel` run concurrently; all others run sequentially |

The global mode (`options.tool_execution_mode`) defaults to `Batch`. Individual tools may override via `tool.execution_mode`.

For each tool call:

1. Resolve the tool by name. If not found, return `ToolResultContent(is_error=True)`.
2. Run `options.before_tool_call(invocation, signal)`. If a blocked result is returned, skip execution.
3. Emit `tool_execution_start`.
4. Call `tool.execute(tool_call_id, params, signal, on_update)`.
5. Emit `tool_execution_update` for each `on_update` call (streaming tool progress).
6. Emit `tool_execution_end` with the final result.
7. Run `options.after_tool_call(invocation, result, signal)` to let callers patch the result.

Errors thrown by `tool.execute()` are caught and returned as `ToolResultContent(is_error=True)`.

If every result in a batch has `terminate=True`, the loop exits after `turn_end` without making another LLM call.

## Steering and follow-up queues

Two message queues hold mid-run injections:

**Steering queue** — drained after tool results are collected, before the next LLM call. The agent mid-flight pivot: inject a message while tools are running and the LLM will see it on the next turn.

**Follow-up queue** — drained only when the LLM stops without tool calls. Use this to queue a follow-up prompt that runs immediately after the current work is done.

Queue modes control how many messages are dequeued per drain:

| Mode | Behaviour |
|---|---|
| `OneAtATime` | Dequeue the single oldest message |
| `All` | Dequeue and inject all queued messages |

Messages are injected by emitting `message_start` + `message_end` for each, then appending them to `messages`. This makes them visible to the LLM on the next turn.

External callers inject via:
- `engine.steer(message)` — adds to steering queue
- `engine.follow_up(message)` — adds to follow-up queue

The queues can be cleared via `clear_steering()`, `clear_follow_up()`, or `clear_all_queues()`.

## Abort

`engine.abort()` sets the abort signal (`asyncio.Event`). The loop checks the signal:

- After assembling steering messages: if set, emit `turn_end` and break.
- After each tool batch via `signal.is_set()`.

The signal is a plain `asyncio.Event`. There is no timeout or cancellation of in-flight LLM streams — abort is cooperative.

## Events emitted by Engine

| Event | When |
|---|---|
| `agent_start` | Once, before the first turn |
| `agent_end` | Once, after the loop exits |
| `agent_error` | When a turn ends with `Error` or `Abort` stop reason |
| `turn_start` | At the start of each LLM call |
| `turn_end` | After the LLM call and any tool execution for that turn |
| `message_start` | When a message begins (user, assistant, tool-result) |
| `message_update` | Streaming assistant content (text or thinking deltas) |
| `message_end` | When a message is complete |
| `tool_execution_start` | When a tool begins |
| `tool_execution_update` | Streaming tool progress |
| `tool_execution_end` | When a tool finishes |

Events are dispatched via `process_events()`, which calls every registered subscriber in registration order. Subscribers are awaited sequentially.

## Options

```python
@dataclass
class Options:
    tool_execution_mode: ToolExecutionMode = ToolExecutionMode.Batch
    steering_mode: SteeringMode = SteeringMode.OneAtATime
    followup_mode: FollowupMode = FollowupMode.OneAtATime

    before_tool_call: BeforeToolCallCallback | None = None
    after_tool_call: AfterToolCallCallback | None = None
    on_event: OnEventCallback | None = None
    get_steering_messages: GetSteeringMessagesCallback | None = None
    get_follow_up_messages: GetFollowUpMessagesCallback | None = None
    should_stop_after_turn: ShouldStopAfterTurnCallback | None = None
    transform_context: TransformContextCallback | None = None
    get_ephemeral_messages: GetEphemeralMessagesCallback | None = None
```

Agent sets `before_tool_call`, `after_tool_call`, `on_event`, and `get_ephemeral_messages` at construction time. The others are available for lower-level callers.

### get_ephemeral_messages

`get_ephemeral_messages: Callable[[], Awaitable[list[LLMMessage]]]`

Called at the start of every turn, before the LLM call. The returned messages are appended to `ctx_messages` for that call only — they are never written to `state.messages` or persisted to the session. Exceptions are suppressed.

Agent uses this to inject live desktop and browser state (window/tab info, DOM tree, optional screenshot) so the LLM always sees fresh context without it polluting the session history.

## run_continue()

`run_continue()` resumes from the existing `state.messages` without a new user prompt. The last message must not be an assistant message. If there are steering or follow-up messages queued, they are injected first.

## Related documents

- [agent.md](./agent.md) — Agent orchestration above Engine
- [hooks.md](./hooks.md) — Event types and Hooks wiring
- [inference.md](./inference.md) — LLM streaming API

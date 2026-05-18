# Gateway

The Gateway module routes user input from multiple transport channels through the Runtime and streams agent events back to the originating channel. It is the recommended integration point when you need to connect the agent to non-terminal transports (WebSocket, HTTP, RPC) or when you want a clean event-driven interface over the REPL.

## Architecture

```
Gateway
  ├── asyncio.Lock          — serialise concurrent sends (one turn at a time)
  ├── dict[channel_id, BaseChannel]  — registered channels
  └── Runtime               — underlying agent runtime

send(channel_id, text)
  └── acquires lock, sets _active_channel
  └── subscribes to agent.hooks
  └── runtime.user_input(text)
        └── hook events → _handle_event → channel.on_event(GatewayEvent)
  └── channel.on_event(GatewayEvent(type='done'))
  └── releases lock, unsubscribes
```

## Channels

A channel is any class that extends `BaseChannel` and implements two methods:

| Method | Purpose |
|--------|---------|
| `channel_id: str` | Unique identifier for this connection/session |
| `on_event(event: GatewayEvent)` | Receive and handle agent events |

Channels register themselves with `gateway.register(channel)` and submit user input by calling `await gateway.send(channel_id, text)`.

## Event types

Every agent event is mapped to a `GatewayEvent` with a `type` and a `data` dict:

| type | data keys | fired when |
|------|-----------|-----------|
| `stream_start` | — | agent begins a new turn |
| `chunk` | `text`, `kind` (`"text"` or `"thinking"`) | streaming text arrives |
| `stream_end` | — | assistant message complete |
| `tool_start` | `name`, `args` | tool call begins |
| `tool_end` | `name`, `result`, `is_error` | tool call finishes |
| `error` | `message` | agent or gateway error |
| `done` | — | full invocation complete |

## Concurrency model

The Gateway uses a single `asyncio.Lock`. While one channel's turn is active, all other `send()` calls from other channels are suspended at the lock. This means:

- The agent always processes one prompt at a time (matching the underlying `Agent` assumption).
- Channels see a clean stream with no interleaving from other clients.
- Cron-triggered invocations go through `runtime.invoke()` directly and bypass the Gateway lock — they do not stream to any channel.

## Built-in channels

### `StdioChannel`

Terminal rendering with ANSI colour, identical to what `main.py` does with a raw hook subscriber. Use it when building a Gateway-backed REPL.

```python
from program.gateway import Gateway, StdioChannel
from program.runtime import Runtime, RuntimeConfig

runtime = await Runtime.create(RuntimeConfig(cwd=Path.cwd(), model_id='claude-sonnet-4-6'))
gateway = Gateway(runtime)

channel = StdioChannel()
gateway.register(channel)

# then in the REPL loop:
await gateway.send(channel.channel_id, user_input)
```

### `WebSocketChannel`

One instance per client connection. Sends/receives JSON over a websockets connection.

```python
from program.gateway import Gateway, serve_websocket

gateway = Gateway(runtime)

# Runs until cancelled
await serve_websocket(gateway, host='127.0.0.1', port=8765)
```

#### JSON protocol

**Server → client:**

```json
{"type": "stream_start"}
{"type": "chunk", "text": "Hello", "kind": "text"}
{"type": "chunk", "text": "...", "kind": "thinking"}
{"type": "stream_end"}
{"type": "tool_start", "name": "web_search", "args": {"query": "..."}}
{"type": "tool_end",   "name": "web_search", "result": "...", "is_error": false}
{"type": "error",      "message": "Something went wrong"}
{"type": "done"}
```

**Client → server:**

```json
{"type": "message", "text": "What's the weather?"}
```

## Writing a custom channel

```python
from program.gateway.types import BaseChannel, GatewayEvent

class MyChannel(BaseChannel):
    def __init__(self, id: str):
        self._id = id

    @property
    def channel_id(self) -> str:
        return self._id

    async def on_event(self, event: GatewayEvent) -> None:
        if event.type == 'chunk' and event.data.get('kind') == 'text':
            print(event.data['text'], end='', flush=True)
        elif event.type == 'done':
            print()

# register and use:
channel = MyChannel('my-channel')
gateway.register(channel)
await gateway.send('my-channel', 'hello')
```

## Running WebSocket and REPL together

```python
import asyncio
from program.gateway import Gateway, StdioChannel, serve_websocket

gateway = Gateway(runtime)
stdio = StdioChannel()
gateway.register(stdio)

# Run WebSocket server as a background task
ws_task = asyncio.create_task(serve_websocket(gateway, port=8765))

# REPL loop
while True:
    text = input('[You] ').strip()
    if text in ('/quit', '/exit'):
        break
    await gateway.send(stdio.channel_id, text)

ws_task.cancel()
runtime.shutdown()
```

## Related documents

- [agent.md](./agent.md) — `invoke()` and `PromptOptions.source`
- [cron.md](./cron.md) — Cron-triggered agent invocations
- [session.md](./session.md) — Session lifecycle

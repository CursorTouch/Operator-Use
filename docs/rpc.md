# RPC server

`RPCServer` exposes the `Runtime` over a bidirectional JSONL protocol. Commands arrive on stdin; responses and agent events flow out on stdout. Every line is a self-contained JSON object terminated by a single LF.

This makes the agent usable as a subprocess by any language that can read/write JSON lines.

## Wire format

**Command** (stdin):
```json
{"type": "prompt", "id": "req-1", "text": "explain this file"}
```

**Response** (stdout):
```json
{"type": "response", "command": "prompt", "id": "req-1", "success": true, "data": {}}
```

**Event** (stdout, async):
```json
{"type": "event", "event": {"type": "message_update", "message": {"role": "assistant", ...}}}
```

The `id` field on commands is optional. When present it is echoed back in the response so the caller can correlate responses to commands.

## Commands

| Command | Fields | Description |
|---|---|---|
| `prompt` | `text`, `options?` | Run a user turn with the given text |
| `compact` | `custom_instructions?` | Run compaction now (agent must be idle) |
| `abort` | — | Abort the current turn |
| `new_session` | — | Start a new session |
| `fork` | `entry_id` | Fork the session from a specific entry |
| `switch_session` | `session_file` | Switch to a different session file |
| `get_state` | — | Return current agent state snapshot |

All commands return a response with `success: true` on completion or `success: false, error: "..."` on failure.

## Events

The server subscribes to Engine events and writes them to stdout as they occur:

```json
{"type": "event", "event": {"type": "agent_start"}}
{"type": "event", "event": {"type": "message_update", "message": {...}}}
{"type": "event", "event": {"type": "message_end", "message": {...}}}
{"type": "event", "event": {"type": "agent_end", "messages": [...]}}
```

Events are serialized with `_to_jsonable()`, which recursively converts dataclasses, Pydantic models, enums (to `.value`), and `Path` objects (to string).

## Serialization

`_to_jsonable()` handles:

- `None`, `bool`, `int`, `float`, `str` — pass through
- `enum.Enum` — `.value`
- `Path` — `str()`
- dataclasses — `dataclasses.asdict()` recursively
- Pydantic models — `.model_dump()` recursively
- `dict`, `list`, `tuple` — recurse
- anything else — `str()`

## Starting the server

```python
from operator_use.rpc.server import RPCServer
from operator_use.runtime.service import Runtime
from operator_use.runtime.types import RuntimeConfig

config = RuntimeConfig(cwd="/my/project")
runtime = await Runtime.create(config)
server = RPCServer(runtime)
await server.serve()   # reads stdin until EOF
```

`serve()` reads commands line by line. Each line is parsed as JSON and dispatched to the appropriate Runtime method. The server is single-threaded on commands: one command runs at a time. Agent events are emitted concurrently via the subscriber callback.

## RPC client

`program/rpc/client.py` provides a Python client that wraps the subprocess protocol. It is useful for testing and for embedding the agent in another process.

## Related documents

- [agent.md](./agent.md) — Agent methods the RPC server calls
- [engine.md](./engine.md) — Events the RPC server forwards to stdout
- [hooks.md](./hooks.md) — Full event type list

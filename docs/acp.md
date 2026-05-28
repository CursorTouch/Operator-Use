# ACP (Agent Communication Protocol)

Operator exposes a full ACP server so external tools (IDEs, other agents, remote machines) can drive it over a standard protocol. Three transports are available: **stdio** for local subprocess use, **HTTP** for authenticated remote connections, and **WebRTC** for peer-to-peer remote machine communication.

## Transports

### Stdio transport

`ACPStdioServer` serves `OperatorACPAgent` over stdin/stdout. All ACP JSON-RPC framing goes to stdout; all logging must go to stderr. This is the standard entry point for IDE integrations (Zed, Claude Code CLI, Codex CLI) and same-machine inter-agent communication via subprocess.

```
operator acp serve [--cwd <path>] [--model <id>] [--provider <name>] [--sandbox off|warn|enforce|strict]
```

The gateway is disabled in this mode (`RuntimeConfig.gateway=False`) — no Telegram/Discord/Slack channels start up.

### HTTP transport

`ACPHttpServer` serves the same agent over HTTP so remote machines can connect.

```
operator acp serve-http [--host 0.0.0.0] [--port 8080] [--cwd <path>] [--model <id>] [--provider <name>]
```

#### HTTP endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/acp/events` | SSE stream (server → client messages). Returns `X-ACP-Connection-ID` header. |
| `POST` | `/acp/rpc/{conn_id}` | Client → server JSON-RPC messages. |
| `POST` | `/acp/auth/device` | Step 1: request a device code to start auth flow. |
| `POST` | `/acp/auth/approve` | Step 2: owner approves a pending device code (run on server machine). |
| `POST` | `/acp/auth/token` | Step 3: client polls until it receives the access token. |

All `/acp/events` and `/acp/rpc/*` requests require `Authorization: Bearer <token>`.

#### Remote auth flow (device flow)

```
# 1 — client requests a code
POST /acp/auth/device
→ { device_code, user_code, verification_uri, expires_in, interval }

# 2 — owner approves (on the server)
POST /acp/auth/approve  { "device_code": "..." }
→ { status: "approved", access_token: "..." }

# 3 — client polls until approved
POST /acp/auth/token  { "device_code": "..." }
→ { access_token: "...", token_type: "Bearer" }
   or { error: "authorization_pending" }

# 4 — client connects
GET /acp/events  Authorization: Bearer <token>
```

#### HTTP bridge internals

The HTTP server bridges SSE↔ACP using an asyncio socket pair per connection:
- The SSE handler creates the socket pair, wraps it in an `AgentSideConnection`, and streams JSON-RPC events as `data: <json>` SSE events.
- POST `/rpc/{conn_id}` writes the body to the corresponding `bridge_writer`, which feeds the `AgentSideConnection` as incoming messages.

### WebRTC transport

`ACPWebRTCServer` serves the same agent over a WebRTC DataChannel. PeerJS-compatible signaling is used only for room rendezvous and SDP exchange; ACP JSON-RPC lines flow over the peer-to-peer DataChannel after the connection is established.

```
operator acp serve-webrtc my-room [--cwd <path>] [--model <id>] [--provider <name>]
operator acp connect webrtc:my-room
```

For settings-based agents, use `transport: "webrtc"` and put the room name in `url`.

## Interactive client

`operator acp connect` opens an interactive REPL against any ACP agent:

```
operator acp connect <target>

# TARGET forms:
#   <agent_id>          resolve from settings.json acp.agents
#   stdio:<cmd> [args]  spawn a subprocess
#   http://<url>        connect to a remote HTTP server
#   webrtc:<room>       connect to a remote WebRTC room
```

## Python client (`ACPClient`)

```python
from operator_use.acp.client import ACPClient

# Stdio subprocess
client = ACPClient.stdio('operator', 'acp', 'serve')

# HTTP (with optional Bearer token)
client = ACPClient.http('http://remote:8080', token='my-token')

# WebRTC peer-to-peer transport
client = ACPClient.webrtc('my-room')

# Resolve from settings.json acp.agents
client = ACPClient.discover('operator')

async with client:
    async with client.session(cwd='/my/project') as session_id:
        # Non-streaming
        result = await client.run('summarize this repo', session_id)

        # Streaming
        async for chunk in client.run_stream('what is the main module?', session_id):
            print(chunk, end='', flush=True)
```

Permission handling in `OperatorACPClient.request_permission()`: prefers `allow_always` → `allow_once` → first available option. Falls back to `DeniedOutcome` if no options are present.

## Server capabilities

`OperatorACPAgent` implements:

| Method | Description |
|---|---|
| `initialize` | Declare capabilities and protocol version |
| `new_session` | Create a fresh agent session |
| `load_session` | Resume an existing session by ID |
| `resume_session` | Re-attach to a session after disconnect |
| `fork_session` | Branch a session to a new ID |
| `list_sessions` | List known sessions (currently returns empty list) |
| `close_session` | Destroy a session |
| `cancel` | Cancel an in-progress turn |
| `prompt` | Run a user turn and stream the response |
| `set_session_mode` | No-op (hook for future modes) |
| `set_session_model` | No-op (hook for future model switching) |
| `set_config_option` | No-op (hook for config) |
| `ext_method` / `ext_notification` | Extension hooks |

Session capabilities declared: `close`, `list`, `resume`.

## Built-in `claude` agent

When `operator` is on `PATH`, the `acp_agent` tool automatically registers a built-in agent named `claude` that spawns `operator acp serve`. No settings.json configuration is required.

When dispatching tasks to the built-in `claude` agent, the tool inherits the parent's provider and model (preferring `anthropic-claude-code` if available) and passes them as `--provider` / `--model` flags to the subprocess.

## Settings

ACP agents are configured under the `acp` key in `settings.json` (replaces the old flat `acp_agents` list):

Install the npm ACP adapters once:

```bash
sudo npm install -g @agentclientprotocol/codex-acp
sudo npm install -g @agentclientprotocol/claude-agent-acp
```

```json
{
  "acp": {
    "enabled": true,
    "agents": [
      {
        "name": "codex",
        "enabled": true,
        "transport": "stdio",
        "command": "codex-acp",
        "args": []
      },
      {
        "name": "claude-code",
        "enabled": true,
        "transport": "stdio",
        "command": "claude-agent-acp",
        "args": []
      },
      {
        "name": "operator-child",
        "enabled": true,
        "transport": "stdio",
        "command": "operator",
        "args": ["acp", "serve"]
      },
      {
        "name": "remote-worker",
        "enabled": true,
        "transport": "http",
        "url": "http://worker-machine:8080"
      },
      {
        "name": "remote-peer",
        "enabled": true,
        "transport": "webrtc",
        "url": "my-room"
      }
    ]
  }
}
```

The `@agentclientprotocol/*` packages install ACP adapter binaries. They run as stdio ACP servers, so Operator starts `codex-acp` or `claude-agent-acp` and communicates over stdin/stdout.

`ACPAgentConfig` fields:

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | required | Unique name used in `acp_agent(agent='...')` |
| `enabled` | `bool` | `true` | Skip this entry when `false` |
| `transport` | `'stdio' \| 'http' \| 'webrtc'` | `'stdio'` | Wire transport |
| `command` | `str \| None` | `None` | Executable for stdio transport |
| `args` | `list[str]` | `[]` | Extra CLI arguments for stdio |
| `url` | `str \| None` | `None` | Base URL for HTTP transport, or room name for WebRTC |

`SettingsManager` API for ACP:

| Method | Description |
|---|---|
| `get_acp_agents()` | Enabled `ACPAgentConfig` entries (empty if `acp.enabled=False`) |
| `get_acp_settings()` | Full `ACPSettings` with defaults |
| `get_acp_agent_config(name)` | Look up one entry by name |
| `set_acp_enabled(enabled)` | Toggle the whole ACP block |
| `set_acp_agent_config(name, **kwargs)` | Upsert an agent entry |
| `remove_acp_agent_config(name)` | Delete an agent entry |

## ACP session persistence

`ACPSessionManager` tracks session IDs for each named agent so that follow-up tasks resume the same remote context. Session files are stored at:

```
~/.operator/profiles/<name>/acp/<agent_name>.json
```

**Session persistence requires an active profile.** When the agent is started without `--profile`, `ACPSessionManager` operates in-memory only — `save()` is a no-op, `get()` always returns `None`, and `list()` returns `[]`. No `acp/` directory is created in `~/.operator/`.

## Related documents

- [inference.md](./inference.md) — Model and provider resolution (used by built-in `claude` agent)
- [gateway.md](./gateway.md) — How `acp_agent` tool results are delivered back through the bus
- [auth.md](./auth.md) — `ACPAuthManager` for HTTP Bearer token storage per agent

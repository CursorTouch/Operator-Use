# Gateway

The Gateway module connects external channels (Telegram, Discord, Slack, WebSocket, Email, Twitch, stdio) to the Runtime. Each channel has its own session per conversation. Messages arrive as `IncomingMessage`, are processed by an Agent, and responses are streamed back as `OutgoingMessage` events.

## Architecture

```
                   ┌─────────────────────────────────┐
Channel            │           GatewayManager         │
  .connect()  ───► │  Gateway ◄──── Bus ◄──── Agent  │
  .receive()  ───► │    │                             │
  .send()     ◄─── │    └─── _outgoing_loop           │
                   └─────────────────────────────────┘
```

Three cooperating pieces:

**Bus** — two `asyncio.Queue` instances. Channels `publish_incoming` messages; the Gateway consumes them and publishes `outgoing` responses; channels consume outgoing messages in their `send()`.

**Gateway** — runs two background loops:
- `_incoming_loop`: dequeues `IncomingMessage`, looks up or creates a per-`channel:chat_id` Agent session, spawns an asyncio Task to run it.
- `_outgoing_loop`: dequeues `OutgoingMessage`, routes to the matching `BaseChannel.send()`.

**GatewayManager** — owned by the Runtime. Owns the Bus, Gateway, and all channel asyncio Tasks. `start()` creates tasks for every enabled channel; `stop()` cancels them.

## Channel interface

Every channel is a subclass of `BaseChannel`:

```python
class BaseChannel(ABC):
    bus: Bus | None          # set by Gateway.register()

    @property
    @abstractmethod
    def channel_id(self) -> str: ...

    async def connect(self) -> None: ...        # run until cancelled
    async def disconnect(self) -> None: ...     # release resources

    async def receive(self, msg: IncomingMessage) -> None:
        await self.bus.publish_incoming(msg)    # push to bus

    @abstractmethod
    async def send(self, msg: OutgoingMessage) -> None: ...
```

Channels register with `gateway.register(channel)`, which sets `channel.bus` and fires `ChannelConnectEvent`. A channel then calls `connect()` to begin receiving from its transport (polling, WebSocket server, long-poll, etc.).

## Message types

### IncomingMessage

```python
@dataclass
class IncomingMessage:
    channel: str        # "telegram", "discord", "slack", "ws:connid", "stdio", ...
    chat_id: str        # unique conversation within the channel
    parts: list[ContentPart]
    user_id: str = ""
    message_id: str = ""   # channel-side ID (for reactions, reply threading)
    metadata: dict[str, Any] = {}
    timestamp: datetime
```

### OutgoingMessage

```python
@dataclass
class OutgoingMessage:
    channel: str
    chat_id: str
    parts: list[ContentPart] = []
    stream_phase: StreamPhase | None = None
    metadata: dict[str, Any] = {}
    timestamp: datetime
```

### StreamPhase

`stream_phase` controls how channels render output:

| Phase | Meaning |
|---|---|
| `START` | A new agent turn has begun — start typing indicator, clear stale tool-status slot |
| `CHUNK` | Streaming content: `metadata['kind']` is `'text'`, `'thinking'`, `'tool_start'`, `'tool_update'`, or `'tool_end'` |
| `END` | Assistant message complete — flush buffered text, stop typing indicator |
| `DONE` | Full turn complete (including TTS injection) |
| `ERROR` | Turn ended with an error |
| `None` | Out-of-band message: file, audio, intermediate text, or reaction |

#### CHUNK metadata by kind

| `kind` | Extra metadata fields | Meaning |
|---|---|---|
| `'text'` | — | Streaming assistant text |
| `'thinking'` | — | Streaming thinking/reasoning text |
| `'tool_start'` | `name`, `args`, `id`, `tool_kind` | Tool call beginning |
| `'tool_update'` | `name`, `text`, `id` | Mid-execution status update from the tool (emitted by `tool_execution_update_callback`) — channels edit the rolling status message in-place |
| `'tool_end'` | `name`, `id`, `is_error: bool`, `result: str` | Tool call complete |

### Rolling tool-status messages

For Telegram, Discord, and Slack, tool progress is shown through a **single shared message** per chat that is edited in-place rather than posting a new message for each event:

- `tool_start` → post "⚙️ `<name>`…" (or edit the existing slot)
- `tool_update` → edit the rolling status message in-place with the latest status text
- `tool_end` (success) → edit to "✅ `<name>`"
- `tool_end` (error) → edit to "❌ `<name>`\n`<result>`"
- First text `CHUNK` → delete the rolling status message (the response replaces it)
- `END` (final, `keep_typing=False`) → delete any leftover status message
- Retry success → delete the status message from the failed attempt

### Thinking stream

Extended thinking tokens are streamed into the same rolling-status slot:

- `thinking` chunk → accumulate text; start a debounced loop that edits "💭 `<thinking…>`" (capped at 800 chars)
- `tool_start` or text chunk → stop the thinking loop, clear the buffer

### ContentPart types

| Class | Fields | Use |
|---|---|---|
| `TextPart` | `content: str` | Text |
| `AudioPart` | `audio: str` (file path), `mime_type` | Voice messages or TTS output |
| `FilePart` | `path: str`, `mime_type` | Files and documents |
| `ImagePart` | `images: list[str]`, `paths`, `mime_type` | Images |

`text_from_parts(parts)` joins all `TextPart.content` values with newlines.

### Out-of-band metadata (`stream_phase=None`)

When `stream_phase` is `None`, `metadata` carries routing hints:

| `metadata['kind']` | Fields | Meaning |
|---|---|---|
| `'react'` | `message_id`, `emoji` | Add emoji reaction to a specific message |
| *(absent)* | `reply_to` | Direct send (file, audio, text) with optional reply-to |

## Session model

Each conversation gets its own `Agent` instance, keyed by `channel:chat_id`:

```
"telegram:987654321"          → Agent (Telegram DM with that user/group)
"slack:C123456:1680000000.0"  → Agent (Slack channel/thread)
"ws:4398046511104"            → Agent (WebSocket connection)
```

If a session is already processing a message when the next one arrives, the new text is steered into the running agent via `engine.steer()` rather than queuing a new turn.

`gateway.cancel_session(channel_id, chat_id)` hard-cancels an in-progress session.

## Hook points

The Gateway fires hook events at two points in the message pipeline:

### `message:receive` — before the agent processes input

Fired after an `IncomingMessage` arrives but before it reaches the Agent. Handlers receive `MessageReceiveEvent` and may return `MessageReceiveResult`:

```python
# action='continue'  — pass through (default)
# action='transform' — replace parts / text (used by the STT builtin hook)
# action='reject'    — drop the message; optional reason sent back to channel
```

The builtin **STT hook** (`operator_use/builtins/hooks/stt.py`) handles this: it detects `AudioPart` entries, transcribes them using the configured STT model, and returns `MessageReceiveResult(action='transform', parts=[TextPart(transcript)])`.

### `message:send` — after the agent finishes

Fired after the agent completes its turn, before the `DONE` frame. Handlers receive `MessageSendEvent` and may return `MessageSendResult(parts=[...])` to inject additional content (e.g., TTS audio).

The builtin **TTS hook** (`operator_use/builtins/hooks/tts.py`) handles this: it synthesizes speech from `event.response_text` and returns `MessageSendResult(parts=[AudioPart(...)])`. `event.is_voice` lets TTS hooks gate synthesis on whether the user spoke vs. typed.

## Built-in channels

### StdioChannel

Terminal REPL with ANSI colour output. Used by `operator_use.console.main:cli` for the interactive CLI. Sends `IncomingMessage` when the user presses Enter; renders streaming chunks in real time.

### WebSocketChannel / WebSocketServer

One `WebSocketChannel` per connected client. `WebSocketServer` listens for connections and spawns a channel per client.

```python
from operator_use.gateway.channels.websocket import WebSocketServer
server = WebSocketServer(gateway, host='127.0.0.1', port=8765)
await server.start()
```

#### WebSocket JSON protocol

**Client → server:**
```json
{"type": "message", "text": "Hello!", "message_id": "client-assigned-id"}
```

**Server → client:**
```json
{"type": "start"}
{"type": "chunk", "text": "Hello", "kind": "text"}
{"type": "chunk", "text": "...", "kind": "thinking"}
{"type": "chunk", "kind": "tool_start", "name": "web_search", "args": {...}}
{"type": "chunk", "kind": "tool_update", "name": "web_search", "text": "Fetching results…", "id": "..."}
{"type": "chunk", "kind": "tool_end", "name": "web_search", "is_error": false, "result": ""}
{"type": "chunk", "kind": "tool_end", "name": "web_search", "is_error": true, "result": "timeout"}
{"type": "end"}
{"type": "done"}
{"type": "error", "text": "Something went wrong"}
{"type": "message", "text": "Progress update...", "reply_to": "msg-id"|null}
{"type": "file", "filename": "report.pdf", "mime_type": "application/pdf", "data": "<b64>", "caption": "...", "reply_to": "msg-id"|null}
{"type": "react", "message_id": "client-assigned-id", "emoji": "👍"}
```

### TelegramChannel

Polls via PTB (python-telegram-bot ≥ 20.0). Handles text, voice messages, and audio files. Shows a typing indicator while the agent works.

**Setup:** Set `TELEGRAM_BOT_TOKEN` env var or `auth/channels.json`. Enable `message_content` intent. Enable the channel in settings (`channels.telegram.enabled = true`).

**Markdown pipe tables** are automatically wrapped in fenced code blocks before HTML conversion so they render as monospace in Telegram (which does not support native table markup).

`show_thinking` defaults to `false` for Telegram, Discord, and Slack. Set it to `true` per channel to stream model thinking text into the same rolling status message used before tool calls.

### DiscordChannel

Uses discord.py ≥ 2.0. Responds to DMs and @mentions. Handles text and audio attachments. Shows a typing indicator while the agent works. On startup, syncs Operator slash commands as Discord application commands and routes command invocations through the same runtime command registry used by the REPL and Telegram.

**Setup:** Set `DISCORD_BOT_TOKEN` env var or `auth/channels.json`. Enable `message_content` intent in the Discord Developer Portal. Invite the bot with the `applications.commands` scope so slash commands are visible. Enable the channel in settings.

### SlackChannel

Uses slack-bolt ≥ 1.0 via Socket Mode. Handles `app_mention` events and DMs. Also registers Socket Mode listeners for Operator slash commands and routes them through the runtime command registry when Slack sends the command payload. `chat_id` format: `"slack_channel_id:thread_ts"` if in a thread, else just `"slack_channel_id"`.

**Setup:** Set `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN` env vars or `auth/channels.json`. Enable Socket Mode in Slack App settings. Add the slash commands you want in the Slack app configuration; Slack does not expose a Bot-API-style command-menu sync like Telegram. Enable the channel in settings.

### TwitchChannel

IRC-over-WebSocket bot. Responds to commands prefixed with a configurable prefix (default `!`). Optionally restricts to specific usernames via `allow_from`.

**Setup:** Set `TWITCH_TOKEN` env var or `auth/channels.json`. Configure `channels.twitch.channel_name` and `channels.twitch.nick` in settings.

### EmailChannel

IMAP polling (stdlib `imaplib`) + SMTP sending (stdlib `smtplib`). Polls `INBOX` for `UNSEEN` messages at a configurable interval. Threads replies using `In-Reply-To` / `References` headers.

`chat_id` = the thread root `Message-ID` (stable across the whole thread).

**Setup:** Set `EMAIL_USERNAME` + `EMAIL_PASSWORD` env vars or `auth/channels.json`. Configure `imap_host` and `smtp_host` in settings.

## Startup flags

Two flags inject an initial message when the gateway or REPL starts, useful for
quick-starting a task or resuming after a programmatic reboot.

| Flag | Commands | Description |
|---|---|---|
| `--prompt "..."` | `operator`, `operator gateway`, `operator gateway run`, `operator repl` | Inject text as the first user message immediately on startup — the agent processes it without waiting for human input |
| `--session-file <path>` | same (hidden flag) | Open a specific session JSONL file instead of creating a new one. Used internally by the `control_center` reboot action to restore the exact session |

```bash
# Quick-start with a task
operator --prompt "summarise everything in the docs folder"
operator repl --prompt "run the test suite and fix any failures"

# Resume a specific session (normally handled automatically by reboot)
operator gateway run --session-file ~/.operator/profiles/<name>/sessions/abc123.jsonl
```

## Channel settings

All channels are configured in `settings.json` under the `channels` key:

```json
{
  "channels": {
    "telegram":  { "enabled": false, "show_thinking": false },
    "discord":   { "enabled": false, "show_thinking": false },
    "slack":     { "enabled": false, "show_thinking": false },
    "twitch":    { "enabled": false, "channel_name": "", "nick": "", "prefix": "!", "allow_from": [] },
    "email":     { "enabled": false, "imap_host": "", "imap_port": 993, "smtp_host": "", "smtp_port": 587, "poll_interval": 30, "allow_from": [] },
    "websocket": { "enabled": false, "host": "127.0.0.1", "port": 8765 }
  }
}
```

Channel credentials (tokens, passwords) are stored separately in `auth/channels.json` — see [auth.md](./auth.md).

## The `send` tool

The `send` builtin tool lets the agent publish content to the active channel mid-turn:

| `mode` | Required fields | Effect |
|---|---|---|
| `file` | `path` | Upload a local file; optional `caption` |
| `intermediate` | `text` | Send a status update without ending the turn |
| `react` | `emoji` | Add an emoji reaction to the user's triggering message |

All modes accept:
- `message_id` — target a specific message instead of the default (the user's triggering message)
- `reply=True` (file/intermediate) — send as a reply/thread

```
# Example tool call
send(mode="file", path="/tmp/report.pdf", caption="Here's the analysis")
send(mode="react", emoji="✅")
send(mode="intermediate", text="Fetching page 3 of 10…")
```

Channel-specific emoji notes:
- **Slack**: use the name without colons, e.g. `"thumbsup"`, `"white_check_mark"`
- **Telegram**: must be one of the 74 allowed reaction emojis
- **Discord**: any standard emoji character

## control_center tool

The `control_center` builtin lets the agent inspect and update runtime settings
without human involvement.

**`action="get"`** — read one or all settings:
```
control_center, action="get"                          # all settings
control_center, action="get", key="computer_use"
```

**`action="set"`** — update a setting and apply it. Feature-flag changes trigger
`runtime.reload()` so the tool list is rebuilt immediately:
```
control_center, action="set", key="computer_use", value={"enabled": true}
control_center, action="set", key="default_model", value="claude-opus-4-7"
```

**`action="reboot"`** — flush settings, gracefully shut down services, and replace
the process with a fresh instance via `os.execv`. Pass `resume_prompt` to continue
a task automatically after restart:
```
control_center, action="reboot",
  resume_prompt="Reboot complete. Continue: run the test suite."
```

The reboot appends `--session-file` and `--prompt` to `sys.argv` before `execv`
so the fresh process opens the exact same session and injects the continuation
message — no intermediate files, no new session created.

Controllable settings:

| Key | Type | Reload? |
|---|---|---|
| `cron_enabled` | bool | yes |
| `subagents_enabled` | bool | yes |
| `workflows_enabled` | bool | yes |
| `computer_use` | `ComputerUseSettings` | yes |
| `browser_use` | `BrowserUseSettings` | yes |
| `extensions_enabled` | bool | yes |
| `compaction_enabled` | bool | no |
| `retry_enabled` | bool | no |
| `default_provider` | str | no |
| `default_model` | str | no |

## Writing a custom channel

```python
from operator_use.gateway.types import BaseChannel
from operator_use.bus.types import IncomingMessage, OutgoingMessage, StreamPhase, TextPart

class MyChannel(BaseChannel):
    @property
    def channel_id(self) -> str:
        return "mychannel"

    async def connect(self) -> None:
        # Run until cancelled — poll a source, open a server socket, etc.
        async for message in my_source():
            await self.receive(IncomingMessage(
                channel="mychannel",
                chat_id=message.conversation_id,
                parts=[TextPart(message.text)],
                user_id=message.user_id,
                message_id=message.id,
            ))

    async def disconnect(self) -> None:
        pass  # cleanup

    async def send(self, msg: OutgoingMessage) -> None:
        if msg.stream_phase == StreamPhase.END:
            # flush buffered text
            ...
        elif msg.stream_phase is None:
            # out-of-band: file, audio, reaction
            ...
```

Register and start it:

```python
from operator_use.gateway.manager import GatewayManager

# via GatewayManager (in Runtime context)
gateway_manager.gateway.register(MyChannel())

# or standalone
channel = MyChannel()
gateway.register(channel)
asyncio.create_task(channel.connect())
```

## Related documents

- [hooks.md](./hooks.md) — Gateway hook events and STT/TTS result types
- [auth.md](./auth.md) — Channel credential storage (`ChannelAuthManager`)
- [agent.md](./agent.md) — Agent session lifecycle
- [acp.md](./acp.md) — ACP transport (stdio/HTTP) for IDE and remote-agent connections

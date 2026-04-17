# Operator

A multi-platform desktop automation agent framework. Users send messages through channels (Telegram, Discord, Slack, Twitch, MQTT); the agent controls the desktop, browses the web, and runs tools using an LLM. Think of it as a personal AI assistant with eyes and hands on the machine.

**Package:** `operator_use/` — main source. Entry: `main.py` → `operator_use/cli/commands.py`.

---

## Message Flow

```
Channel
  └─► Bus (incoming queue)
        └─► Orchestrator (STT, message building, routing)
              └─► Agent (LLM loop + tool execution)
                    └─► Bus (outgoing queue)
                          └─► Gateway → Channel (sends response)
```

1. A channel receives a user message and pushes an `IncomingMessage` onto the bus.
2. The **Orchestrator** consumes it: runs STT if it's a voice message, converts parts to a `HumanMessage`/`ImageMessage`, then routes to the correct Agent.
3. The **Agent** runs the LLM agentic loop up to `max_iterations`: if the LLM returns a tool call, it executes the tool and loops again; if it returns text, an `AIMessage` is returned.
4. The Orchestrator converts the response to an `OutgoingMessage` (running TTS if the user spoke), then publishes it to the outgoing bus queue.
5. The **Gateway** dispatch loop picks it up and sends it back through the originating channel.

---

## Module Map

| Module | What it does |
|---|---|
| `bus/` | Two async queues: `incoming` (channel → agent) and `outgoing` (agent → channel). Decouples everything. |
| `gateway/` | Manages all channel instances. Routes inbound messages to bus; dispatches outbound messages to the right channel. Channels live in `gateway/channels/`. |
| `orchestrator/` | Pipeline layer between bus and agents. Owns STT/TTS, message construction, agent routing, and the main `ainvoke()` consume loop. Also handles session control commands (`/start`, `/stop`, `/restart`) and pending-reply coordination. |
| `agent/` | LLM agentic loop. Receives a pre-built message, builds system prompt + history, calls LLM, handles tool calls iteratively. Has no knowledge of channels, bus, or STT/TTS. |
| `context/` | Builds the full system prompt from workspace files (SOUL.md, RULES.md, MEMORY.md, skills index, knowledge index, CODE.md). |
| `providers/` | Adapters for 14+ LLMs (OpenAI, Anthropic, Google, Mistral, Groq, Ollama, Cerebras…) and STT/TTS providers. All implement `BaseChatLLM` / `BaseSTT` / `BaseTTS`. |
| `computer/` | Desktop control via native accessibility APIs. **Windows:** Windows UI Automation (UIA) via `comtypes`/`pywin32`. **macOS:** Accessibility Framework via `pyobjc` (`ax/` module). **Linux:** WIP. Exposed as tools via plugins. |
| `tools/` | Built-in tool implementations (filesystem, terminal, web search/browse). Auto-registered at startup. |
| `session/` | Per-user conversation history (`SessionStore` → `Session`). Persisted as `.jsonl` files at `.operator_use/sessions/{channel}_{chat_id}.jsonl`. |
| `config/` | Pydantic settings loaded from `.operator_use/config.json`, merged with `OPERATOR_` env vars. One config class per channel and provider. |
| `messages/` | Core message models: `HumanMessage`, `AIMessage`, `ImageMessage`, `ToolMessage`. |
| `plugins/` | Optional capability bundles (e.g. `computer_use`, `browser_use`). Each registers tools and hooks on an Agent at init time, and can be enabled/disabled at runtime. |
| `skills/` | Markdown procedural guides. Loaded from `workspace/skills/{name}/SKILL.md`. Available immediately without restart. |
| `acp/` | ACP (Agent Communication Protocol) — multi-agent server/client. Routes messages to named agents with per-agent token isolation. |
| `subagent/` | Spawns child agents for parallel or delegated tasks within a single agentic loop. |
| `crons/` | Cron-scheduled tasks. Persisted to `.operator_use/crons.json`. When `deliver=True`, publishes `OutgoingMessage` directly to bus at fire time. |
| `heartbeat/` | Periodic maintenance loop (~30 min). Calls `orchestrator.process_direct()` with `HEARTBEAT.md` as the prompt so the agent can do background upkeep. |
| `interceptor/` | Pre/post message hooks. `RestartInterceptor` handles self-improvement restart recovery by reading `restart.json` on startup. |
| `process/` | Manages long-running background subprocesses launched by tools. |
| `web/` | Web browsing and search tools (DuckDuckGo, HTTP fetch, page scraping). |

---

## Agent Loop Detail

```python
# Non-streaming (agent/service.py _loop)
for iteration in range(max_iterations):
    messages = await context.build_messages(history, ...)  # system prompt + history
    llm_event = await llm.ainvoke(messages, tools)
    if llm_event.type == TOOL_CALL:
        result = await tool_registry.aexecute(tool_call.name, tool_call.params)
        session.add(ToolMessage)      # loop continues
    elif llm_event.type == TEXT:
        session.add(AIMessage)
        return AIMessage              # done
```

Hooks fire at `BEFORE/AFTER_AGENT_START/END`, `BEFORE/AFTER_LLM_CALL`, and `BEFORE/AFTER_TOOL_CALL`. Plugins register tools and hooks on the Agent at init. Tool extensions (like `_channel`, `_chat_id`, `_workspace`, `_gateway`) are injected into the registry so tools have access to runtime context.

**Streaming** works identically but uses `llm.astream()`: chunks are published to the bus as `StreamPhase.START → CHUNK → END → DONE`, allowing channels like Telegram and Discord to edit a message in-place as text arrives.

---

## Computer Module (Desktop Control)

### macOS — `computer/macos/ax/`

A Pythonic wrapper over native macOS Accessibility (`AXUIElement`), Quartz (`CGEvent`), and `NSWorkspace`. Organized in layers:

```
core.py      — low-level AXUIElement/CGEvent functions
controls.py  — typed Control classes (ButtonControl, TextFieldControl, WindowControl…)
patterns.py  — InvokePattern, ValuePattern, ScrollPattern, TogglePattern…
events.py    — EventObserver for watching AX notifications
enums.py     — Role, Attribute, Action, Notification, KeyCode constants
```

Entry points: `GetFrontmostApplication()` → `ApplicationControl`, `GetForegroundControl()` → `WindowControl`, `GetRunningApplications()` → list. Controls expose typed methods: `btn.Click()`, `field.SendKeys("text")`, `window.Resize(w, h)`, `ax.HotKey('command', 'c')`. Requires macOS Accessibility permission.

### Windows — `computer/windows/`

Uses Windows UI Automation (UIA) via `comtypes` + `pywin32`. Also includes VDM (Virtual Display Manager) for virtual screen management and a watchdog for system change detection.

---

## Workspace (runtime directory)

The agent reads and can self-update a `workspace/` directory. Everything here is live — the agent is expected to write to these files as part of normal operation.

| File/Dir | Purpose |
|---|---|
| `SOUL.md` | Agent identity and personality — who it is, not what it can do |
| `RULES.md` | **Immutable** hard constraints: privacy, external actions require confirmation, no destructive ops without explicit confirmation |
| `USER.md` | Learned user profile — name, timezone, preferences |
| `CODE.md` | Agent's self-awareness map: architecture, flows, how to modify itself |
| `AGENTS.md` | Operational guide: session startup sequence, how to use tools, skills, memory, heartbeat, restart |
| `HEARTBEAT.md` | Task list processed every ~30 min. If empty, heartbeat is skipped. |
| `memory/MEMORY.md` | Curated long-term memory. Auto-injected into system prompt. Only loaded in direct sessions (not group chats). |
| `memory/YYYY-MM-DD.md` | Daily append-only log. Written during sessions. Promoted to MEMORY.md during heartbeat. |
| `skills/{name}/SKILL.md` | Procedural skill guides. YAML frontmatter (`name`, `description`) + Markdown instructions. Loaded on trigger. |
| `skills/{name}/.history/` | Auto-snapshotted version history whenever SKILL.md is overwritten. |
| `tools/*.py` | Custom Python tools. Auto-loaded at startup — no restart needed. |
| `knowledge/` | Stable reference docs (`index.md` per topic). Index shown at startup; content loaded on demand. |
| `sessions/` | Serialized conversation history (`.jsonl` per session). |

### Custom Tool Format

```python
# workspace/tools/my_tool.py
from operator_use.tools.service import Tool, ToolResult
from pydantic import BaseModel

class MyParams(BaseModel):
    input: str

@Tool(name="my_tool", description="What this tool does", model=MyParams)
def my_tool(input: str) -> ToolResult:
    result = do_something(input)
    return ToolResult.success_result(result)
```

Async tools (`async def`) are also supported. Name conflicts with built-in tools are skipped with a warning.

### Skill Structure

```
workspace/skills/my-skill/
├── SKILL.md          # required — YAML frontmatter + instructions
├── scripts/          # executable Python/Bash for deterministic ops
├── references/       # docs loaded on demand (schemas, API specs)
└── assets/           # templates, images used in output
```

Skills use progressive disclosure: metadata (~100 tokens) always in context; full body loaded when triggered; bundled resources loaded only as needed.

---

## Restart / Self-Improvement Flow

The agent can edit its own codebase and restart to load changes:

```
restart(continue_with="describe what to do after restart")
  → saves restart.json (task + channel + chat_id)
  → session saved to .jsonl
  → os._exit(75)                   ← supervisor catches exit code
  → supervisor relaunches process
  → on startup: restart.json found, deleted, task dispatched back to channel
  → agent loads existing session (full history intact) and continues
```

Without `continue_with`, the task is lost after restart. Always set it when restart is a step, not the destination.

---

## Heartbeat vs Cron

| | Heartbeat | Cron |
|---|---|---|
| Timing | ~30 min, can drift | Exact schedule (`9:00 AM Mon`) |
| Context | Has recent session context | Isolated, no session history |
| Use for | Batched checks (email, calendar, memory maintenance) | One-shot reminders, exact-timing deliveries |
| Config | `HEARTBEAT.md` | `crons.json` via `cron` tool |

---

## Adding a Channel

1. **Config** — add config class to `config/service.py` (inherit `Base`, add `enabled: bool`, `allow_from: list`)
2. **Channel class** — create `gateway/channels/{name}.py`, inherit `BaseChannel`, implement `start()`, `stop()`, `_listen()`, `send()`; call `self.receive(incoming_message)` to push inbound messages to the bus
3. **Streaming** — use `StreamPhase` (START, CHUNK, END, DONE) in `send()` to support live message editing
4. **Register** — add to `ChannelsConfig` in `config/service.py`, export from `gateway/channels/__init__.py`, wire in `orchestrator/`

## Adding an LLM Provider

Inherit `BaseChatLLM`, implement `ainvoke()` and optionally `astream()`, then add to `LLM_CLASS_MAP` in `runner.py`.

---

## Data / Config Paths

```
.operator_use/
├── config.json          — active configuration (merged with OPERATOR_* env vars)
├── sessions/            — conversation history per channel+chat_id (.jsonl)
├── crons.json           — persisted cron jobs
├── restart.json         — written before restart; auto-deleted on next startup
└── workspace/           — agent identity, memory, skills, tools, knowledge
```

---

## Tech Stack

- Python 3.13+, `asyncio`, `pydantic` v2, `typer`, `uv`
- LLMs: OpenAI, Anthropic, Google, Mistral, Groq, Ollama, Cerebras
- Channels: `python-telegram-bot`, `discord.py`, `slack-bolt`, `twitchio`, `aiomqtt`
- Desktop: `comtypes` + `pywin32` (Windows UIA), `pyobjc` (macOS Accessibility)

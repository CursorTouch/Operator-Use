# Operator

A stateful Python AI agent harness with multi-provider LLM support, session persistence, context compaction, extensible tool system, and a gateway that connects the agent to multiple messaging channels simultaneously.

**Build autonomous, persistent agents that remember context, self-correct on errors, and integrate seamlessly with your tools and workflows.**

## Features at a Glance

| Feature | What it does |
|---------|-------------|
| **Multi-provider LLM** | Claude, GPT-4, Gemini, Mistral, Ollama, GitHub Copilot |
| **Session persistence** | JSONL-based branching tree — resume, fork, replay |
| **Context compaction** | Auto-summarize long conversations to fit token limits |
| **Extensions & packages** | Hook into agent lifecycle, add tools, register commands |
| **Skill injection** | Teach the agent strategies via markdown docs |
| **Tool framework** | Parallel, streaming, async tool execution |
| **Browser & desktop** | CDP automation + native OS control (macOS, Linux, Windows) |
| **Multi-channel gateway** | Telegram, Discord, Slack, Email, Twitch, WebSocket, stdio |
| **Workflows** | Multi-phase async DSL for orchestrating complex tasks |
| **Teams** | Persistent multi-agent coordination via mailboxes |
| **Sandbox** | Filesystem, network, and shell execution policies |
| **Memory** | Long-term fact store (Mem0, SuperMemory) across sessions |
| **MCP & ACP** | Model Context Protocol servers, Agent Communication Protocol |

## Architecture

```
Runtime (session lifecycle, slash commands, channels)
  ├── GatewayManager         ← Telegram, Discord, Slack, Email, Twitch, WebSocket, stdio
  ├── WorkflowManager        ← Multi-phase async task orchestration
  ├── TeamManager            ← Persistent multi-agent coordination
  ├── SubagentManager        ← Ephemeral parallel agents
  ├── CronScheduler          ← Scheduled recurring tasks
  ├── ProcessManager         ← Background shells and agents
  └── Agent (orchestration)
        ├── Engine            ← LLM streaming, tool execution, abort signals
        ├── SessionManager    ← JSONL persistence, branching, context reconstruction
        ├── ExtensionRuntime  ← Hook dispatch to extensions
        ├── ResourceLoader    ← Tools, skills, commands, extensions
        ├── Compaction        ← Token budget, auto-summarization
        ├── MemoryManager     ← Long-term cross-session facts
        ├── Sandbox           ← Policy enforcement
        └── Knowledge         ← System prompt injection
```

**Four-layer design:**
- **Engine** — raw LLM loop, tool execution, no session knowledge
- **Agent** — turn orchestration, retry, compaction scheduling, extension event fan-out
- **Runtime** — session lifecycle, slash-command dispatch, gateway/cron/subagent wiring
- **Gateway** — channel adapters, async message bus, per-session agent routing

## Quick start

### Python API
```python
from operator_use.runtime.service import Runtime
from operator_use.runtime.types import RuntimeConfig

# Create and run an agent in your project
config = RuntimeConfig(cwd="/my/project")
runtime = await Runtime.create(config)
response = await runtime.user_input("explain the architecture of this codebase")
print(response)  # Agent's response
```

### CLI
```bash
# Start interactive agent in current project
operator

# Use a different model or provider
operator --model claude-opus-4-8
operator --model gpt-4o --provider openai

# Start with a named profile (has its own settings, sessions, tools)
operator --profile coder

# Interactive Python REPL with an agent
operator --repl

# Configure defaults
operator set --model claude-opus-4-8 --provider anthropic
operator unset --model --provider  # reset to defaults
```

### Multi-Channel Deployment
```bash
# Serve as ACP (Agent Communication Protocol) server
operator acp serve              # stdio (pipe to other processes)
operator acp serve-http         # HTTP (port 8080 by default)
operator acp serve-webrtc room  # WebRTC (peer-to-peer rooms)

# Connect to a remote ACP agent
operator acp connect stdio:remote-agent
```

### Example: Custom Extension

**~/.operator/profiles/researcher/extensions/arxiv_fetch.py**
```python
from pydantic import BaseModel
from operator_use.tool.types import ToolResult

class PaperParams(BaseModel):
    query: str
    limit: int = 5

async def fetch_papers(params, invocation, ctx):
    # Integration: call arxiv API, return results
    return ToolResult.ok(invocation.id, f"Found {params.limit} papers on {params.query}")

def extension(api):
    api.register_tool({
        "name": "arxiv",
        "description": "Search arXiv for papers",
        "parameters": PaperParams,
        "execute": fetch_papers,
    })
    # Hook into session events
    api.on("session_start", lambda e, ctx: print("New research session"))
```

Then configure in **~/.operator/settings.json**:
```json
{
  "extension_list": [
    {"name": "arxiv_fetch", "enabled": true, "settings": {"api_key": "..."}}
  ]
}
```

## Agent Profiles

Named profiles give the agent its own system prompt, tool allowlist, model override, and per-profile resource directories (sessions, skills, tools, extensions, knowledge, workflows, teams).

Profiles are defined by `AGENT.md` files placed in `~/.operator/profiles/<name>/`:

```markdown
---
name: coder
description: Senior software engineer focused on Python and TypeScript.
model: claude-opus-4-7
provider: anthropic
tools: read, edit, write, grep, glob, terminal
---

You are a senior software engineer who writes clean, minimal code.
```

Per-profile resource layout:

```
~/.operator/profiles/<name>/
  AGENT.md          ← profile definition
  settings.json     ← per-profile settings overlay
  SOUL.md           ← persona override
  USER.md           ← user profile override
  sessions/         ← session history
  tools/            ← tools
  skills/           ← skills
  extensions/       ← extensions
  knowledge/        ← knowledge docs
  workflows/        ← workflows
  teams/            ← team state
  acp/              ← ACP session files
```

Start with a profile:

```bash
operator --profile coder --cwd /my/project
```

Ephemeral profiles (for subagents) auto-delete on process exit. Named profiles are durable.

## Channels

The gateway routes messages between the agent and multiple channels concurrently. Each channel runs in its own task; all share a single async message bus.

| Channel | Config key | Notes |
|---|---|---|
| stdio | — | Terminal I/O, always available |
| WebSocket | `websocket` | Client or server mode |
| Telegram | `telegram` | Bot API |
| Discord | `discord` | Bot with slash-command sync |
| Slack | `slack` | Socket Mode (`xoxb-` + `xapp-`), slash-command listeners |
| Twitch | `twitch` | Chat integration |
| Email | `email` | SMTP/IMAP |

Channel credentials go in `~/.operator/auth/channels.json` or environment variables (`TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`, `SLACK_BOT_TOKEN`, etc.).

## Providers and models

Multi-provider LLM support. Default model: `claude-sonnet-4-6`.

| Provider | Auth | Models |
|---|---|---|
| Anthropic | API key / OAuth | Claude 4 Opus, Sonnet, Haiku |
| OpenAI | API key / OAuth | GPT-4o, GPT-4 Turbo |
| Google | OAuth | Gemini 2.0 Flash, Pro |
| Mistral | API key | Mistral Large, Small |
| Ollama | Local | Any locally running model |
| GitHub Copilot | OAuth | GPT-4o via Copilot API |

Override per-session: `RuntimeConfig(model_id="claude-opus-4-7", provider="anthropic")`.

## Session persistence

Sessions are stored as JSONL files under the active profile's `sessions/` directory. Each entry has a `parent_id`, forming a tree that supports branching and forking without overwriting history.

```
~/.operator/profiles/<name>/sessions/
  2026-05-20T10-30-00_<uuid>.jsonl
```

When no profile is active, sessions fall back to `~/.operator/sessions/<cwd-hash>/`.

The session file is only written to disk once an `AssistantMessage` exists. Failed retries are rewound — the user message is only permanently removed if all retry attempts are exhausted.

`build_session_context()` walks the root-to-leaf path and respects compaction summaries: the LLM sees a summary of everything before the cut point, the retained tail, and the full post-compaction history.

## Compaction

When `context_tokens > context_window - reserve_tokens`, a separate LLM call summarizes old history into a `CompactionEntry`. The next turn receives the summary plus the retained recent tail instead of the full history.

Compaction always runs after a successful turn (`save_point`), never mid-turn. Extensions can cancel or replace the result via `session_before_compact`.

## Sandbox

The sandbox enforces a `SandboxPolicy` before every tool call.

```bash
operator --sandbox strict    # lock writes to CWD; OS-level sandbox on shell tool
operator --sandbox enforce   # Python-level policy only
operator --sandbox warn      # log violations, never block
operator --sandbox off       # (default) no restrictions
```

Modes:
- **off** — no restrictions (default)
- **warn** — log violations to stderr, never block
- **enforce** — block policy violations before tool execution
- **strict** — preset: writes locked to CWD + OS-level subprocess sandboxing

OS-level sandbox:
- **macOS** — `sandbox-exec` (Apple Seatbelt), no root required
- **Linux** — `bwrap` (bubblewrap), no root required
- **Windows** — Python-level only

## Browser automation

The `browser` tool controls a Chrome or Edge browser via Chrome DevTools Protocol (CDP):

```python
# open
{ "action": "open", "browser": "chrome", "headless": false }
# navigate
{ "action": "goto", "url": "https://example.com" }
# interact
{ "action": "click", "x": 400, "y": 300 }
{ "action": "type", "x": 400, "y": 200, "text": "hello", "press_enter": true }
# read DOM
{ "action": "scrape" }
# close
{ "action": "close" }
```

| Action | Purpose |
|---|---|
| `open` | Launch or attach to a browser |
| `close` | Shut down the browser |
| `goto` | Navigate to a URL |
| `back` / `forward` | Browser history |
| `click` | Click at coordinates |
| `type` | Type text at coordinates |
| `key` | Press a key or key combination |
| `scroll` | Scroll the page |
| `menu` | Select dropdown options |
| `upload` | Upload files |
| `tab` | Open, close, or switch tabs |
| `wait` | Wait for a duration |
| `script` | Execute JavaScript |
| `scrape` | Extract the current page's text/DOM |
| `download` | Download a file from a URL |

The browser session injects its current state (URL, title, open tabs) as an ephemeral message into the LLM context at the start of each turn, so the model always knows the browser's current state without a separate call.

Attach to an existing Chrome DevTools session:

```python
{ "action": "open", "attach_to_existing": true, "cdp_port": 9222 }
```

## Computer control

The `computer` tool controls the local desktop with platform-native accessibility APIs:

| Action | Purpose |
|---|---|
| `open` | Enable desktop access |
| `close` | Release desktop access |
| `click` | Click at screen coordinates |
| `type` | Type text into focused element |
| `wait` | Wait for a duration |
| `app` | Launch, switch, resize, or move an application window |
| `scroll` | Scroll at coordinates |
| `move` | Move the mouse |
| `drag` | Drag from one point to another |
| `shortcut` | Press a keyboard shortcut |

Platform support:
- **macOS** — Accessibility API
- **Linux** — not yet supported
- **Windows** — UI Automation

Like the browser tool, the computer tool injects a compact live state description into the LLM context each turn (focused app, window layout, etc.).

Enable in `settings.json`:

```json
{ "computer_use_enabled": true }
```

## Extensions

Extensions are Python files that hook into agent lifecycle events, register tools, and add slash commands. Drop a `.py` file in `~/.operator/profiles/<name>/extensions/` (or `<project>/.operator/extensions/`) and it loads automatically on the next startup or `/reload`.

```python
# ~/.operator/profiles/<name>/extensions/my_ext.py
from pydantic import BaseModel
from operator_use.extension.types import ToolDefinition
from operator_use.tool.types import ToolResult

class Params(BaseModel):
    name: str

async def _execute(params, invocation, ctx):
    return ToolResult.ok(invocation.id, f"Hello, {params.name}!")

def extension(api):
    # read per-extension settings from settings.json
    strict = api.config.get("strict", False)

    api.on("session_start", lambda event, ctx: None)
    api.register_tool(ToolDefinition(
        name="greet", description="Greet someone.", parameters=Params, execute=_execute,
    ))
    api.register_command("greet", my_handler, description="Greet via command")
```

### Per-extension configuration

Control extensions in `~/.operator/settings.json`:

```json
{
  "extensions": true,
  "extension_list": [
    {
      "name": "git_guard",
      "path": "~/.operator/profiles/<name>/extensions/git_guard.py",
      "enabled": true,
      "author": "jeomon",
      "settings": { "strict": true }
    },
    {
      "name": "noisy_ext",
      "path": "~/.operator/profiles/<name>/extensions/noisy_ext.py",
      "enabled": false
    }
  ]
}
```

`extensions: false` disables all extensions globally. Individual extensions can be toggled via `enabled`. Settings passed in `settings` are available inside the extension via `api.config`.

## Packages

Packages bundle extensions, skills, and prompts for sharing. Install from PyPI, git, or a local path:

```python
from operator_use.package.installer import install_package
from operator_use.settings.paths import get_packages_dir

result = install_package("pypi:my-tools==1.2.3", get_packages_dir())
result = install_package("git:github.com/jeomon/my-tools", get_packages_dir())
result = install_package("/local/path/to/my-tools", get_packages_dir())
```

PyPI packages are installed with `uv pip install --target` (falling back to `pip`) and must ship their `operator.json` and resource dirs at the wheel root. See [docs/packages.md](docs/packages.md).

A package needs an `operator.json` manifest:

```json
{
  "name": "my-tools",
  "author": "jeomon",
  "extensions": ["extensions"],
  "skills": ["skills"]
}
```

If no manifest is present, the directories `extensions/`, `skills/`, and `prompts/` are used by convention.

Installed packages are tracked in `settings.packages`. Their resource dirs are merged into the extension and skill scan on every startup and `/reload`.

## Skills

Skills are markdown files injected into the system prompt. Place a `SKILL.md` in any directory inside `~/.operator/profiles/<name>/skills/` or `<project>/.operator/skills/`:

```markdown
---
name: my-skill
description: Teaches the agent how to handle X.
---

When working on X, always do Y.
```

Skills are discovered at four levels: builtins → global user → project → extension-provided paths.

The `skill` tool lets the agent view, create, edit, patch, and delete skills from within a session without restarting.

## Knowledge base

Knowledge documents are markdown files that are indexed and injected into the system prompt. Place `.md` files in `~/.operator/knowledge/` or `<project>/.program/knowledge/`:

```
~/.operator/knowledge/
  company.md            → name "company"
  products/index.md     → name "products"
  api/v2/index.md       → name "api/v2"
```

Project-level docs take precedence over global ones when names collide.

The agent sees a compact index in the system prompt and can load the full content on demand.

## Memory

Provider-agnostic long-term memory that persists facts across sessions:

```json
{
  "memory": {
    "enabled": true,
    "provider": "mem0",
    "max_prompt_chars": 6000,
    "sync_turns": true,
    "prefetch": true
  }
}
```

Install optional memory dependencies:

```bash
uv pip install ".[memory]"
```

| Provider | Env var |
|---|---|
| `mem0` | `MEM0_API_KEY` |
| `supermemory` | `SUPERMEMORY_API_KEY` |

The `memory` tool lets the agent search recalled context, store new facts, and remove memories:

```python
{ "action": "search", "query": "user's preferred code style" }
{ "action": "remember", "content": "User prefers tabs over spaces in Python." }
{ "action": "forget", "memory_id": "<provider-id>" }
```

## Built-in tools

| Tool | Kind | Description |
|---|---|---|
| `read` | Read | Read file contents |
| `write` | Write | Create or overwrite a file |
| `edit` | Edit | Apply targeted edits to a file |
| `ls` | Read | List directory contents |
| `glob` | Read | Match file patterns |
| `grep` | Read | Search text in files |
| `terminal` | Execute | Run shell commands with streaming output |
| `computer` | Execute | Control the local desktop (click, type, scroll, apps, shortcuts) |
| `browser` | Execute | Automate a Chrome/Edge browser via CDP |
| `process` | Execute | Start shell commands or background agent sessions |
| `web_fetch` | Web | Fetch and parse a web page |
| `web_search` | Web | Search the web |
| `memory` | Unknown | Search, store, and remove long-term memories |
| `todo` | Unknown | Manage a per-session task list |
| `skill` | Write | View, create, edit, patch, and delete agent skills |
| `cron` | Execute | Schedule recurring agent tasks |
| `mcp` | Execute | Connect to MCP servers and call their tools |
| `subagent` | Execute | Spawn an ephemeral subagent |
| `workflow` | Execute | Generate, run, and manage Python workflows |
| `team` | Execute | Create and coordinate persistent multi-agent teams |
| `peer_agent` | Execute | Delegate tasks to a named peer agent (another profile) |
| `send` | Execute | Send a message to a gateway channel |
| `acp_agent` | Execute | Invoke a remote ACP agent |
| `control_center` | Unknown | Read/write runtime settings, trigger reboot |

Add custom tools by dropping a `.py` file in `~/.operator/profiles/<name>/tools/` or `<project>/.operator/tools/`:

```python
# .operator/tools/my_tool.py
from operator_use.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult
from pydantic import BaseModel

class Params(BaseModel):
    path: str

class MyTool(Tool):
    def __init__(self):
        super().__init__(name="my_tool", description="...", schema=Params,
                         kind=ToolKind.Read, execution_mode=ToolExecutionMode.Parallel)

    async def execute(self, invocation: ToolInvocation, callback=None, signal=None) -> ToolResult:
        return ToolResult.ok(invocation.id, "result")

tool = MyTool()
```

## Process manager

The `process` tool manages two kinds of background processes:

**Shell processes** — run arbitrary commands with captured output:

```python
# start
{ "action": "start", "command": "npm run dev", "description": "dev server" }
# read output (last N bytes from in-memory ring buffer)
{ "action": "output", "process_id": "<id>", "max_bytes": 8000 }
# stop
{ "action": "stop", "process_id": "<id>" }
```

**Agent processes** — spawn a background `operator acp serve` session with multi-turn interaction:

```python
# spawn
{ "action": "spawn_agent", "prompt": "watch the logs and alert on errors",
  "description": "log watcher", "provider": "anthropic", "model": "claude-sonnet-4-6" }
# send follow-up prompts
{ "action": "write", "process_id": "<id>", "prompt": "now check /var/log/app.log" }
# read disk log
{ "action": "output", "process_id": "<id>" }
```

Shell output is stored in an in-memory ring buffer (1 MB cap per process). Agent output is written to a disk log file under the active profile's `temp/` directory.

## Workflows

Workflows are Python files that orchestrate multi-step tasks using a built-in async DSL:

```python
# ~/.operator/profiles/<name>/workflows/research.py
meta = {
    "name": "research",
    "description": "Research a topic and produce a report.",
}

async def run():
    async with phase("gather"):
        log("Gathering sources…")
        sources = await agent("find 5 authoritative sources on {topic}")

    async with phase("write"):
        log("Writing report…")
        report = await agent(f"write a report using these sources:\n{sources}")

    return report
```

Workflow DSL globals (injected at runtime — do not import):

| Global | Description |
|---|---|
| `await agent(prompt, schema=None)` | Run an LLM call; returns str or a Pydantic model |
| `await parallel(*thunks, concurrency=5)` | Run zero-arg async callables concurrently |
| `await pipeline(items, *stages, concurrency=5)` | Process items through a pipeline of stages |
| `async with phase("name"):` | Label the current workflow phase in the status |
| `log("message")` | Append a timestamped line to the run log |
| `budget` | `Budget` object — `.remaining()`, `.spent()`, `.exhausted()` |
| `args` | `dict` of key-value arguments passed at invocation |

Invoke via the `workflow` tool:

```python
{ "action": "run", "name": "research", "args": { "topic": "LLM context windows" } }
{ "action": "generate", "name": "summarize", "description": "Summarize a set of documents." }
{ "action": "list" }
{ "action": "status", "run_id": "<id>" }
{ "action": "cancel", "run_id": "<id>" }
```

## Teams

Teams coordinate multiple agent workers that communicate via persistent mailboxes:

```python
# create a team
{ "action": "create", "team_name": "research-team", "description": "Parallel research workers" }
# spawn a member (runs as a subagent using a named profile)
{ "action": "spawn", "team_name": "research-team", "member_name": "Alice",
  "role": "researcher", "task": "research climate change impacts" }
# send a message to a member's inbox
{ "action": "send", "team_name": "research-team", "agent_id": "<id>", "message": "focus on 2025 data" }
# read a member's inbox (clears it)
{ "action": "inbox", "team_name": "research-team", "agent_id": "<id>" }
# check team status
{ "action": "status", "team_name": "research-team" }
# dissolve
{ "action": "dissolve", "team_name": "research-team" }
```

Team state is persisted across restarts at `~/.operator/profiles/<name>/teams/`.

## Peer agents

Peer agents are named profiles running in the same Operator process. Unlike subagents (anonymous, ephemeral), peer agents have durable session history on both sides.

```python
# delegate a task — starts a new session
{ "action": "run", "profile": "coder", "prompt": "refactor the auth module" }
# continue the previous session
{ "action": "run", "profile": "coder", "prompt": "now add tests", "resume": true }
# list profiles
{ "action": "profiles" }
```

Session bookmarks are stored at `~/.operator/profiles/<caller>/peer/<target>.json`. The full conversation history lives on both sides:
- `profiles/<caller>/peer/sessions/<target>/` — caller's outgoing view
- `profiles/<target>/peer/sessions/<caller>/` — target's incoming view

Circular delegation (A → B → A) is blocked automatically.

## Built-in commands

| Command | Description |
|---|---|
| `/compact [instructions]` | Run compaction immediately |
| `/session` | Session management (list, switch, fork, branch) |
| `/reload` | Hot-reload tools, skills, commands, hooks, and extensions |
| `/auth` | Show auth status for all providers |
| `/help` | List all available commands |

Add custom commands by placing a `.py` file in `~/.operator/profiles/<name>/commands/` (or `<project>/.operator/commands/`) exporting a `SlashCommandInfo`.

## Subagents

Spawn isolated subagent tasks from inside an extension or tool:

```python
# via the subagent builtin tool
{
  "task": "summarise the diff in the attached file",
  "label": "diff-summary"
}
```

Each subagent runs in its own `RuntimeContext` with its own session. Results are delivered back to the calling session via the message bus. `SubagentManager` enforces a configurable concurrency cap (default: 10) and timeout (default: 300 s).

## Scheduled tasks (Cron)

The `cron` tool schedules recurring agent tasks using standard cron expressions:

```python
# tool call
{
  "action": "add",
  "name": "daily-standup",
  "schedule": "0 9 * * 1-5",
  "payload": "run the daily standup summary"
}
```

Jobs are persisted in the active profile's `crons.json` and survive restarts. Enable cron in `settings.json` with `"cron_enabled": true`.

## MCP (Model Context Protocol)

Connect to MCP servers via the `mcp` tool or `settings.json`:

```json
{
  "mcp_servers": [
    { "name": "filesystem", "transport": "stdio", "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem"] }
  ]
}
```

`MCPManager` keeps one shared connection per server. Reference counting ensures the connection stays alive as long as at least one session is using it.

## ACP (Agent Communication Protocol)

Operator can serve and consume ACP agents over **stdio**, **HTTP**, or **WebRTC**.

Pre-register remote ACP agents in `settings.json`:

```json
{
  "acp": {
    "enabled": true,
    "agents": [
      { "name": "codex", "transport": "stdio", "command": "codex-acp" },
      { "name": "claude-code", "transport": "stdio", "command": "claude-agent-acp" },
      { "name": "remote", "transport": "http", "url": "http://worker:8080" },
      { "name": "peer", "transport": "webrtc", "url": "my-room" }
    ]
  }
}
```

Install ACP adapters:

```bash
sudo npm install -g @agentclientprotocol/codex-acp
sudo npm install -g @agentclientprotocol/claude-agent-acp
```

The built-in `claude` agent is always available — it spawns `operator acp serve` automatically.

Session state is persisted per-agent in the active profile's `acp/` directory.

See [docs/acp.md](docs/acp.md) for the full protocol, transport details, and auth flow.

## Control center

The `control_center` tool lets the agent inspect and change its own runtime settings without a restart, or trigger a deferred reboot:

```python
# read all settings
{ "action": "get" }
# toggle browser automation
{ "action": "set", "key": "browser_use_enabled", "value": true }
# reboot and resume
{ "action": "reboot", "resume_prompt": "continue from where you left off" }
```

Writable keys: `cron_enabled`, `subagents_enabled`, `workflows_enabled`, `computer_use_enabled`, `browser_use_enabled`, `extensions_enabled`, `compaction_enabled`, `retry_enabled`, `default_provider`, `default_model`.

## Settings

Settings are stored at three scopes — global (`~/.operator/settings.json`), profile (`~/.operator/profiles/<name>/settings.json`), and project (`<project>/.operator/settings.json`). More specific values win; nested objects are merged field by field.

Key fields:

```json
{
  "default_model": "claude-sonnet-4-6",
  "default_provider": "anthropic",
  "extensions": true,
  "extension_list": [...],
  "packages": ["git:github.com/user/pkg"],
  "cron_enabled": true,
  "subagents_enabled": true,
  "workflows_enabled": true,
  "computer_use_enabled": false,
  "browser_use_enabled": false,
  "compaction": {
    "enabled": true,
    "reserve_tokens": 16384,
    "keep_recent_tokens": 20000
  },
  "retry": {
    "enabled": true,
    "max_retries": 3,
    "base_delay_ms": 2000
  },
  "memory": {
    "enabled": true,
    "provider": null,
    "max_prompt_chars": 6000,
    "sync_turns": true,
    "prefetch": true
  },
  "stt": { "enabled": true, "model": "whisper-1" },
  "tts": { "enabled": true, "model": "tts-1", "voice": "alloy" },
  "channels": {
    "telegram": { "enabled": true },
    "discord": { "enabled": true }
  },
  "acp": {
    "enabled": true,
    "agents": [...]
  }
}
```

## Directory layout

```
~/.operator/
  settings.json             ← global settings
  auth/
    providers.json          ← LLM provider credentials
    channels.json           ← channel bot tokens
    acp.json                ← ACP agent credentials
  profiles/
    <name>/
      AGENT.md              ← profile definition
      settings.json         ← per-profile settings overlay
      sessions/             ← per-profile session files
      tools/                ← per-profile tools
      skills/               ← per-profile skills
      extensions/           ← per-profile extensions
      commands/             ← per-profile slash commands
      hooks/                ← per-profile hooks
      subagents/            ← per-profile subagent profiles
      knowledge/            ← per-profile knowledge docs
      workflows/            ← per-profile workflows
      teams/                ← team state
      acp/                  ← ACP session files
      peer/                 ← peer session bookmarks
      crons.json            ← cron job store
  packages/                 ← installed packages (git/ and pypi/)

<project>/.operator/            ← loaded automatically when Operator runs in this repo
  settings.json             ← project-level settings (wins over global)
  SYSTEM.md                 ← custom system prompt
  APPEND_SYSTEM.md          ← appended to system prompt
  extensions/               ← project-level extensions
  tools/                    ← project-level tools
  skills/                   ← project-level skills
  commands/                 ← project-level slash commands
  subagents/                ← project-level subagent profiles
  workflows/                ← project-level workflows
  hooks/                    ← project-level hooks
  knowledge/                ← project-level knowledge docs
```

## Docs

| Document | What it covers |
|---|---|
| [docs/agent.md](docs/agent.md) | Agent orchestration — phases, turn flow, retry, compaction scheduling, event wiring |
| [docs/engine.md](docs/engine.md) | Engine loop — LLM streaming, tool execution modes, steering/follow-up queues |
| [docs/session.md](docs/session.md) | Session persistence — JSONL format, tree navigation, branch/fork, context reconstruction |
| [docs/hooks.md](docs/hooks.md) | Hooks system — event types, handler registration, result semantics |
| [docs/extensions.md](docs/extensions.md) | Extension system — loading, dispatch, per-extension config, tool/command registration |
| [docs/packages.md](docs/packages.md) | Packages — installing, bundling, and loading extension/skill packages |
| [docs/compaction.md](docs/compaction.md) | Compaction — budget checks, cut-point selection, split-turn handling |
| [docs/inference.md](docs/inference.md) | Inference layer — LLM, model registry, provider/auth, multi-provider APIs |
| [docs/memory.md](docs/memory.md) | Memory — provider/API registries, long-term memory, runtime integration |
| [docs/gateway.md](docs/gateway.md) | Gateway — channel types, message bus, stream phases, routing |
| [docs/rpc.md](docs/rpc.md) | RPC server — JSONL protocol, command/event shapes |
| [docs/skill.md](docs/skill.md) | Skills — SKILL.md format, discovery order, validation, name collision handling |
| [docs/tool.md](docs/tool.md) | Tools — Tool interface, execution modes, streaming, loading from files |
| [docs/message.md](docs/message.md) | Messages — content types, LLM vs session messages, Usage, image handling |
| [docs/commands.md](docs/commands.md) | Commands — slash command parsing, built-in commands, extension commands |
| [docs/auth.md](docs/auth.md) | Auth — credential types, storage, token refresh, OAuth login/logout |
| [docs/acp.md](docs/acp.md) | ACP — stdio/HTTP/WebRTC transports, server capabilities, client usage, session persistence |
| [docs/browser.md](docs/browser.md) | Browser automation — CDP client, actions, tab management, ephemeral state |
| [docs/computer.md](docs/computer.md) | Computer control — platform backends, action set, watchdog lifecycle |
| [docs/profiles.md](docs/profiles.md) | Agent profiles — AGENT.md format, per-profile resources, ephemeral profiles |
| [docs/sandbox.md](docs/sandbox.md) | Sandbox — policy modes, filesystem/shell/network checks, OS-level isolation |
| [docs/knowledge.md](docs/knowledge.md) | Knowledge — discovery layout, system prompt injection, precedence |
| [docs/team.md](docs/team.md) | Teams — TeamManager, member spawning, mailbox messaging, persistence |
| [docs/workflow.md](docs/workflow.md) | Workflows — DSL globals, phases, budget, parallelism, code generation |

## Key design decisions

**Single event funnel.** The Agent is the only path between Engine events and extensions. Engine emits raw loop events (`agent_start`, `turn_start`, `message_end`, …) via `options.on_event`. Agent intercepts all of them and fans out to loaded extension handlers. This keeps the Engine free of extension knowledge.

**Session before commit.** Session persistence is deferred — only written once an `AssistantMessage` exists. If a turn fails, Agent rewinds all entries appended during that attempt before retrying. The user message is only removed if all retries are exhausted.

**Compaction at save points.** Compaction runs after `save_point` (durable session writes, agent idle). Never mid-turn. Extensions can cancel or replace the compaction result.

**Ephemeral state injection.** Browser and computer tools inject a compact live-state message into the LLM context at the start of each turn. This message is never written to session history — it's rebuilt fresh each turn and removed afterward, keeping the state accurate without polluting the permanent record.

**Extension errors are non-fatal.** Both `ExtensionRuntime` and `Hooks` catch handler errors, log them, and continue. An extension throwing on any event does not abort the active turn.

**Profile-scoped isolation.** Every resource path (sessions, crons, ACP state, team state, peer bookmarks) is profile-scoped when a profile is active. Without a profile the system operates in a global fallback mode — session persistence works but ACP/team/peer state is in-memory only.

**Two-scope (now three-scope) settings.** Global, profile, and project settings are loaded independently and deep-merged. Project settings win at the field level; nested dataclasses merge field-by-field so partial overrides work.

**Builtin priority.** Builtin tool, command, and skill names take priority over extensions and packages. Extensions can never shadow a builtin silently.

**Sandbox at the boundary.** `Sandbox` hooks into `Hooks` to intercept every tool call before execution. Python-level policy checks run for all tools; OS-level sandboxing wraps the `terminal` tool's subprocess on macOS and Linux.

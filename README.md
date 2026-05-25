# Operator

A stateful Python AI agent harness. Operator wraps a multi-provider LLM inference layer with session persistence, context compaction, an extension system, a package manager, and a gateway that connects the agent to multiple messaging channels simultaneously.

## Architecture

```
Runtime
  ├── CommandRegistry        ← slash-command dispatch
  ├── GatewayManager         ← channel lifecycle (Telegram, Discord, Slack, WebSocket, Email, Twitch, stdio)
  ├── SubagentManager        ← ephemeral subagent task pool
  ├── MCPManager             ← Model Context Protocol server connections
  ├── CronScheduler          ← scheduled agent tasks
  └── Agent                  ← orchestration layer (implements ExtensionContext)
        ├── Engine            ← LLM streaming loop, tool execution, queues, abort signal
        ├── SessionManager    ← JSONL persistence, branching tree, context reconstruction
        ├── ExtensionRuntime  ← event dispatch to extensions and Hooks
        │     └── Hooks       ← typed event bus
        ├── ResourceLoader    ← tools, skills, commands, hooks, extensions, context files
        │     └── PackageLoader ← resolves installed packages → extension/skill dirs
        └── Compaction        ← token budget monitoring, LLM-driven summarization
```

**Engine** drives the raw LLM loop. **Agent** adds session persistence, retry, compaction scheduling, and extension event fan-out. **Runtime** adds session lifecycle, slash-command dispatch, and channel connectivity.

## Quick start

```python
from program.runtime.service import Runtime
from program.runtime.types import RuntimeConfig

config = RuntimeConfig(cwd="/my/project")
runtime = await Runtime.create(config)
await runtime.user_input("explain this codebase")
```

```bash
# CLI
operator --cwd /my/project
operator --model claude-opus-4-7 --cwd /my/project
operator set --model claude-opus-4-7 --provider anthropic
operator unset --model --provider
operator --repl   # interactive Python REPL
```

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

Channel credentials go in `~/.program/auth/channels.json` or environment variables (`TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`, `SLACK_BOT_TOKEN`, etc.).

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

Sessions are stored as JSONL files in `~/.program/agent/sessions/<cwd-hash>/`. Each entry has a `parent_id`, forming a tree that supports branching and forking without overwriting history.

```
~/.program/agent/sessions/<hash>/
  2026-05-20T10-30-00_<uuid>.jsonl
```

The session file is only written to disk once an `AssistantMessage` exists. Failed retries are rewound — the user message is only permanently removed if all retry attempts are exhausted.

`build_session_context()` walks the root-to-leaf path and respects compaction summaries: the LLM sees a summary of everything before the cut point, the retained tail, and the full post-compaction history.

## Compaction

When `context_tokens > context_window - reserve_tokens`, a separate LLM call summarizes old history into a `CompactionEntry`. The next turn receives the summary plus the retained recent tail instead of the full history.

Compaction always runs after a successful turn (`save_point`), never mid-turn. Extensions can cancel or replace the result via `session_before_compact`.

## Extensions

Extensions are Python files that hook into agent lifecycle events, register tools, and add slash commands. Drop a `.py` file in `~/.program/agent/extensions/` and it loads automatically on the next startup or `/reload`.

```python
# ~/.program/agent/extensions/my_ext.py
from pydantic import BaseModel
from program.extension.types import ToolDefinition
from program.tool.types import ToolResult

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

Control extensions in `~/.program/settings.json`:

```json
{
  "extensions": true,
  "extension_list": [
    {
      "name": "git_guard",
      "path": "~/.program/agent/extensions/git_guard.py",
      "enabled": true,
      "author": "jeomon",
      "settings": { "strict": true }
    },
    {
      "name": "noisy_ext",
      "path": "~/.program/agent/extensions/noisy_ext.py",
      "enabled": false
    }
  ]
}
```

`extensions: false` disables all extensions globally. Individual extensions can be toggled via `enabled`. Settings passed in `settings` are available inside the extension via `api.config`.

## Packages

Packages bundle extensions, skills, and prompts for sharing. Install from git or a local path:

```python
from program.package.installer import install_package
from program.settings.paths import get_packages_dir

result = install_package("git:github.com/jeomon/my-tools", get_packages_dir())
result = install_package("/local/path/to/my-tools", get_packages_dir())
```

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

Skills are markdown files injected into the system prompt. Place a `SKILL.md` in any directory inside `~/.program/agent/skills/` or `<project>/.program/agent/skills/`:

```markdown
---
name: my-skill
description: Teaches the agent how to handle X.
---

When working on X, always do Y.
```

Skills are discovered at four levels: builtins → global user → project → extension-provided paths.

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
| `process` | Execute | Start and manage long-running background processes |
| `web_fetch` | Web | Fetch and parse a web page |
| `web_search` | Web | Search the web |
| `cron` | Execute | Schedule recurring agent tasks |
| `mcp` | Execute | Connect to MCP servers and call their tools |
| `subagent` | Execute | Spawn an ephemeral subagent |
| `send` | Execute | Send a message to a gateway channel |
| `acp_agent` | Execute | Invoke a remote ACP agent |

Add custom tools by dropping a `.py` file in `~/.program/agent/tools/` or `<project>/.program/agent/tools/`:

```python
# .program/agent/tools/my_tool.py
from program.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult
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

## Built-in commands

| Command | Description |
|---|---|
| `/compact [instructions]` | Run compaction immediately |
| `/session` | Session management (list, switch, fork, branch) |
| `/reload` | Hot-reload tools, skills, commands, hooks, and extensions |
| `/auth` | Show auth status for all providers |
| `/help` | List all available commands |

Add custom commands by placing a `.py` file in `~/.program/agent/commands/` exporting a `SlashCommandInfo`.

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

Jobs are persisted in `~/.program/crons.json` and survive restarts. Enable cron in `settings.json` with `"cron_enabled": true`.

## Process manager

The `process` tool starts shell commands as background processes and lets the LLM check their output later:

```python
# start
{ "action": "start", "command": "npm run dev", "description": "dev server" }
# read output
{ "action": "read_output", "id": "<process-id>" }
# stop
{ "action": "stop", "id": "<process-id>" }
```

Output is stored in an in-memory ring buffer (1 MB cap per process). Nothing is written to disk.

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

## ACP (Agent Control Protocol)

Pre-register remote ACP agents in `settings.json`:

```json
{
  "acp_agents": [
    { "name": "code-reviewer", "url": "https://my-acp-agent.example.com" }
  ]
}
```

Call them via the `acp_agent` tool. Session state is persisted per-agent in `~/.program/agent/acp/`.

## Settings

Settings are stored at two scopes — global (`~/.program/settings.json`) and project (`<project>/.program/settings.json`). Project values win; nested objects are merged field by field.

Key fields:

```json
{
  "default_model": "claude-sonnet-4-6",
  "default_provider": "anthropic",
  "extensions": true,
  "extension_list": [...],
  "packages": ["git:github.com/user/pkg"],
  "cron_enabled": true,
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
  "stt": { "enabled": true, "model": "whisper-1" },
  "tts": { "enabled": true, "model": "tts-1", "voice": "alloy" },
  "channels": {
    "telegram": { "enabled": true },
    "discord": { "enabled": true }
  }
}
```

## Directory layout

```
~/.program/
  settings.json             ← global settings
  auth/
    providers.json          ← LLM provider credentials
    channels.json           ← channel bot tokens
    acp.json                ← ACP agent credentials
  agent/
    extensions/             ← global user extensions
    tools/                  ← global user tools
    skills/                 ← global user skills
    commands/               ← global user commands
    hooks/                  ← global user hooks
    sessions/               ← per-project session files
    packages/               ← installed packages
      git/github.com/...
  crons.json                ← cron job store

<project>/.program/
  settings.json             ← project-level settings (wins over global)
  SYSTEM.md                 ← custom system prompt
  APPEND_SYSTEM.md          ← appended to system prompt
  agent/
    extensions/             ← project-level extensions
    tools/                  ← project-level tools
    skills/                 ← project-level skills
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
| [docs/memory.md](docs/memory.md) | Memory — provider/API registries, local file memory, runtime integration plan |
| [docs/gateway.md](docs/gateway.md) | Gateway — channel types, message bus, stream phases, routing |
| [docs/rpc.md](docs/rpc.md) | RPC server — JSONL protocol, command/event shapes |
| [docs/skill.md](docs/skill.md) | Skills — SKILL.md format, discovery order, validation, name collision handling |
| [docs/tool.md](docs/tool.md) | Tools — Tool interface, execution modes, streaming, loading from files |
| [docs/message.md](docs/message.md) | Messages — content types, LLM vs session messages, Usage, image handling |
| [docs/commands.md](docs/commands.md) | Commands — slash command parsing, built-in commands, extension commands |
| [docs/auth.md](docs/auth.md) | Auth — credential types, storage, token refresh, OAuth login/logout |

## Key design decisions

**Single event funnel.** The Agent is the only path between Engine events and extensions. Engine emits raw loop events (`agent_start`, `turn_start`, `message_end`, …) via `options.on_event`. Agent intercepts all of them and fans out to loaded extension handlers. This keeps the Engine free of extension knowledge.

**Session before commit.** Session persistence is deferred — only written once an `AssistantMessage` exists. If a turn fails, Agent rewinds all entries appended during that attempt before retrying. The user message is only removed if all retries are exhausted.

**Compaction at save points.** Compaction runs after `save_point` (durable session writes, agent idle). Never mid-turn. Extensions can cancel or replace the compaction result.

**Extension errors are non-fatal.** Both `ExtensionRuntime` and `Hooks` catch handler errors, log them, and continue. An extension throwing on any event does not abort the active turn.

**Two-scope settings.** Global and project settings are loaded independently and deep-merged at startup. Project settings win at the field level; nested dataclasses merge field-by-field so partial overrides work.

**Builtin priority.** Builtin tool, command, and skill names take priority over extensions and packages. Extensions can never shadow a builtin silently.

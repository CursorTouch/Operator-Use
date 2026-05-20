# Operator — CLAUDE.md

Top-level guidance for Claude Code when working on this repository.

## Project Overview

**Operator** is a stateful Python AI agent harness. It wraps a multi-provider LLM inference layer with session persistence, context compaction, an extension/package system, and a gateway that connects the agent to multiple messaging channels simultaneously (Telegram, Discord, Slack, WebSocket, Email, Twitch, stdio).

Four layers build on each other:

1. **Engine** (`program/engine/`) — raw LLM streaming loop, tool execution, abort signal. No knowledge of sessions or extensions.
2. **Agent** (`program/agent/`) — turn orchestration, retry, compaction scheduling, extension event fan-out. Implements `ExtensionContext`.
3. **Runtime** (`program/runtime/`) — session lifecycle, slash-command dispatch, gateway/cron/subagent wiring.
4. **Gateway** (`program/gateway/`) — channel adapters, async message bus, per-session agent routing.

## Repository Layout

```
program/
  agent/         engine/        session/       runtime/
  extension/     hooks/         resource/      gateway/
  inference/     settings/      package/       builtins/
  compaction/    subagent/      cron/          process/
  mcp/           acp/           auth/          tool/
  skill/         commands/      message/       prompt/
tests/
docs/            ← read these before exploring code
```

## Quick Navigation

Find detailed documentation before reading source:

| Need to understand… | Read |
|---|---|
| Turn flow, retry, compaction scheduling | `docs/agent.md` |
| LLM loop, tool execution modes, queues | `docs/engine.md` |
| Session JSONL, branching, reconstruction | `docs/session.md` |
| Extension loading, `api.config`, dispatch | `docs/extensions.md` |
| Package install, `operator.json`, wiring | `docs/packages.md` |
| Event types, hook registration | `docs/hooks.md` |
| Channels, message bus, stream phases | `docs/gateway.md` |
| Models, providers, auth | `docs/inference.md` |
| Tool interface, execution modes | `docs/tool.md` |
| SKILL.md format, discovery | `docs/skill.md` |
| Slash commands, builtin commands | `docs/commands.md` |
| Credentials, OAuth, token refresh | `docs/auth.md` |

## Key Invariants

Violating these breaks the architecture:

- **Layering is strict.** Engine ← Agent ← Runtime. Engine imports nothing from agent or runtime. Never skip a layer.
- **Single event funnel.** Every Engine event reaches extensions through `Agent._on_engine_event()` only. Nothing bypasses it.
- **Session writes are deferred.** Entries only hit disk after the first `AssistantMessage`. Failed retries are fully rewound before the next attempt.
- **Compaction runs at save points.** After `save_point` (turn complete, session durable) — never mid-turn.
- **Builtin names win.** Builtin tool/command/skill names always take priority over extensions and packages. No silent shadowing.
- **Extension errors are non-fatal.** Catch, log, continue. A handler exception must never abort an active turn.

## Common Tasks

**Add a builtin tool** → create `program/builtins/tools/my_tool.py` exporting `tool = MyTool()`.

**Add a user tool** → drop `my_tool.py` in `~/.program/agent/tools/` or `<project>/.program/agent/tools/`.

**Write an extension:**
```python
# ~/.program/agent/extensions/my_ext.py
from pydantic import BaseModel
from program.extension.types import ToolDefinition
from program.tool.types import ToolResult

class Params(BaseModel):
    text: str

async def _run(params, invocation, ctx):
    return ToolResult.ok(invocation.id, params.text.upper())

def extension(api):
    mode = api.config.get("mode", "default")   # set via extension_list in settings.json
    api.on("session_start", lambda e, ctx: None)
    api.register_tool(ToolDefinition(
        name="shout", description="Shout text.", parameters=Params, execute=_run,
    ))
```

**Configure an extension** in `~/.program/settings.json`:
```json
{
  "extensions": true,
  "extension_list": [
    { "name": "my_ext", "enabled": true, "settings": { "mode": "strict" } }
  ]
}
```

**Install a package:**
```python
from program.package.installer import install_package
from program.settings.paths import get_packages_dir

install_package("git:github.com/user/my-tools", get_packages_dir())
# then add the source to settings_manager.set_packages([...])
```

**Add a skill** → create `my-skill/SKILL.md` in `~/.program/agent/skills/` with a `name` and `description` frontmatter block.

**Add a slash command** → create `program/builtins/commands/my_cmd.py` exporting `command = SlashCommandInfo(...)`.

## Behavioral Guidelines

Adapted from [andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills) / [ClaudeForge](https://github.com/alirezarezvani/ClaudeForge). Apply to every coding, review, and refactoring task.

**1. Think before coding.**
State load-bearing assumptions before writing anything. If a request has multiple reasonable interpretations, surface them rather than picking one silently. Stop and ask when something is genuinely unclear.

**2. Simplicity first.**
Write the minimum code that solves the exact problem. No speculative features, no abstractions with a single call site, no error handling for conditions that cannot happen, no unrequested configuration knobs. If the first draft can be half the size, rewrite it.

**3. Surgical changes.**
Touch only what the task requires. Match surrounding style. Every changed line should trace directly to the user's request. Do not refactor adjacent code, rename for style, or remove unrelated dead code unless explicitly asked.

**4. Goal-driven execution.**
Convert vague requests into concrete verifiable success criteria before coding. For multi-step work, state a plan with per-step verification. Running tests beats narrating intent.

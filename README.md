# Program

AI agent toolkit: stateful LLM agent with session persistence, compaction, extensions, and a multi-provider inference layer.

## Architecture overview

```
Runtime
  └── Agent
        ├── Engine          ← low-level LLM loop, tool execution, event streaming
        ├── SessionManager  ← JSONL persistence, tree navigation, compaction entries
        ├── ExtensionRuntime← loads and dispatches to extensions; wraps Hooks
        ├── Hooks           ← typed event bus (register/on/emit)
        ├── ResourceLoader  ← skills, context files, system-prompt discovery
        └── Compaction      ← context-window budget checks, LLM-driven summarization
```

The **Engine** drives a single LLM agentic loop. The **Agent** sits above it and adds session persistence, retry-on-error, extension event dispatch, and compaction scheduling. The **Runtime** adds session lifecycle management and slash-command dispatch on top of Agent.

## Docs

| Document | What it covers |
|---|---|
| [docs/agent.md](docs/agent.md) | Agent orchestration — phases, turn flow, retry, compaction scheduling, extension event wiring |
| [docs/engine.md](docs/engine.md) | Engine loop — LLM streaming, tool execution modes, steering/follow-up queues |
| [docs/session.md](docs/session.md) | Session persistence — JSONL format, tree navigation, branch/fork, context reconstruction |
| [docs/hooks.md](docs/hooks.md) | Hooks system — event types, handler registration, result semantics |
| [docs/extensions.md](docs/extensions.md) | Extension system — loading, dispatch, tool/command registration |
| [docs/compaction.md](docs/compaction.md) | Compaction — budget checks, cut-point selection, split-turn handling |
| [docs/inference.md](docs/inference.md) | Inference layer — LLM, model registry, provider/auth, multi-provider APIs |
| [docs/rpc.md](docs/rpc.md) | RPC server — JSONL protocol, command/event shapes |
| [docs/skill.md](docs/skill.md) | Skills — SKILL.md format, discovery order, validation, name collision handling |
| [docs/tool.md](docs/tool.md) | Tools — Tool interface, execution modes, streaming, loading from files |
| [docs/message.md](docs/message.md) | Messages — content types, LLM vs session messages, Usage, image handling |
| [docs/commands.md](docs/commands.md) | Commands — slash command parsing, built-in commands, extension commands |
| [docs/auth.md](docs/auth.md) | Auth — credential types, storage, token refresh, OAuth login/logout |

## Quick start

```python
from program.runtime.service import Runtime
from program.runtime.types import RuntimeConfig

config = RuntimeConfig(cwd="/my/project")
runtime = await Runtime.create(config)
await runtime.user_input("explain this codebase")
```

## Key design decisions

**Single funnel.** The Agent is the single event funnel between Engine and the extension layer. Engine emits low-level loop events (`agent_start`, `turn_start`, `message_end`, …). Agent intercepts those via `options.on_event` and re-dispatches them to all loaded extension handlers. This keeps the Engine clean of extension knowledge.

**Session before commit.** Session persistence is deferred until the turn succeeds. If the Engine reports an error, Agent rewinds all session entries appended during that attempt and retries from the original state. The user message is only permanently removed if all retries are exhausted.

**Compaction at save points.** Compaction runs after `save_point` — once session writes are durable and the agent is idle. It is never triggered mid-turn. Extensions can cancel or replace the compaction result via `session_before_compact`.

**Extension errors are non-fatal.** Both `ExtensionRuntime` and `Hooks` log extension errors and continue. An extension throwing on any event does not abort the active turn.

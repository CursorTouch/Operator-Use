# Memory

The memory layer provides persistent, cross-session context that is separate from static knowledge documents and raw session history.

## Shape

Memory mirrors the inference registry pattern:

```text
program/memory/
  api/          # backend behavior implementations
  provider/     # provider metadata and provider registry
  manager.py    # active-provider orchestration
  types.py      # shared config/runtime dataclasses
```

Memory providers are declared in `program/builtins/providers/memory.py`.

| Provider | API | Notes |
|---|---|---|
| `mem0` | `Mem0MemoryAPI` | Optional Mem0 SDK adapter |
| `supermemory` | `SupermemoryAPI` | Optional Supermemory SDK adapter |

## Settings

```json
{
  "memory": {
    "enabled": true,
    "provider": null,
    "max_prompt_chars": 6000,
    "sync_turns": true,
    "prefetch": true
  }
}
```

Unset fields use defaults. `provider` selects one active external memory provider at a time; `null` disables provider-backed memory.

External providers are imported lazily. Install optional dependencies before selecting them:

```bash
uv pip install ".[memory]"
```

Environment variables:

| Provider | Env var |
|---|---|
| `mem0` | `MEM0_API_KEY` |
| `supermemory` | `SUPERMEMORY_API_KEY` |

## Why One Provider

Memory keeps one active backend behind a shared API, so deeper recall does not bloat prompts and tool lists with multiple competing provider surfaces.

## Tool

The built-in `memory` tool is provider-agnostic. It exposes one schema with an `action` field:

| Action | Required field | Purpose |
|---|---|---|
| `search` | `query` | Retrieve relevant long-term memories from the active provider |
| `remember` | `content` | Store a durable fact through the active provider |
| `forget` | `memory_id` | Remove a provider memory by ID when supported |

The tool talks only to `MemoryManager`; provider-specific APIs stay inside the adapter.

## Hooks

Memory providers can implement lifecycle hooks. `MemoryManager` forwards these hooks when an active provider exists:

| Hook | Intended call site | Purpose |
|---|---|---|
| `prefetch(query)` | Before a model/API turn | Return recalled context for the current turn |
| `queue_prefetch(query)` | After a turn | Warm retrieval for the next turn |
| `sync_turn(user, assistant)` | After a completed turn | Persist or extract useful turn memory |
| `on_session_end(messages)` | Session shutdown/end | Final extraction or flush |
| `on_pre_compact(messages)` | Before compaction | Preserve insights before context is discarded |
| `on_memory_write(action, target, content)` | When the `memory` tool writes | Mirror explicit writes into provider storage |
| `shutdown()` | Runtime shutdown | Close provider resources |

## Next Integration Points

The first slices add the provider/API surface and common tool. Runtime integration should still wire:

- `MemoryManager.prefetch(user_text)` before each turn
- `MemoryManager.sync_turn(user, assistant)` after each completed response
- `on_pre_compact()` before compaction discards old context

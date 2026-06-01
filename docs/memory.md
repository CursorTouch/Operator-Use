# Memory

The memory layer provides persistent, cross-session context that is separate from static knowledge documents and raw session history.

## Shape

```text
operator_use/memory/
  api/
    base.py           ← BaseMemoryAPI — interface all backends implement
    local.py          ← LocalMemoryAPI — built-in file-backed provider
    mem0.py           ← Mem0MemoryAPI — optional Mem0 SDK adapter
    supermemory.py    ← SupermemoryAPI — optional Supermemory SDK adapter
    hindsight.py      ← HindsightMemoryAPI — optional Hindsight (Vectorize) adapter
    holographic.py    ← HolographicMemoryAPI — local SQLite/FTS5 store with trust scoring
    openviking.py     ← OpenVikingMemoryAPI — optional OpenViking filesystem-memory adapter
    builtins.py       ← API name → class registry entries
    registry.py       ← MemoryAPIRegistry
  provider/
    types.py          ← MemoryProvider descriptor
    registry.py       ← MemoryProviderRegistry
  workflows/
    consolidate.py    ← MemoryConsolidateWorkflow — periodic deduplication
  manager.py          ← MemoryManager — active-provider orchestration
  types.py            ← MemoryOptions, MemoryContext, MemorySearchResult
```

## Scope

Memory is **profile-scoped**. There is no global memory store shared across profiles. Each active profile owns its memory independently:

```
~/.operator/profiles/<name>/memory/memories.jsonl
```

`MemoryContext.project_memory_dir` is set to `profile.memory_dir` at runtime. `LocalMemoryAPI` raises if no profile is active and no explicit `root_dir` is set in `MemoryOptions`.

## Providers

| Provider | API class | Requires |
|---|---|---|
| `local` | `LocalMemoryAPI` | Nothing — built-in, no API key (`fastembed` ships by default for semantic search) |
| `mem0` | `Mem0MemoryAPI` | `mem0ai` package + `MEM0_API_KEY` |
| `supermemory` | `SupermemoryAPI` | `supermemory` package + `SUPERMEMORY_API_KEY` |
| `hindsight` | `HindsightMemoryAPI` | `hindsight-client` package + `HINDSIGHT_API_KEY` (cloud) |
| `holographic` | `HolographicMemoryAPI` | Nothing — built-in, local SQLite + FTS5, no API key |
| `openviking` | `OpenVikingMemoryAPI` | `openviking` package + a running server (`OPENVIKING_ENDPOINT`) or embedded mode |

Providers are declared in `operator_use/builtins/providers/memory.py`.

## Settings

Memory settings live in the profile's `settings.json` (not global — memory is profile-specific):

```json
{
  "memory": {
    "enabled": true,
    "provider": "local",
    "sync_turns": true,
    "prefetch": true,
    "max_prompt_chars": 6000
  }
}
```

| Field | Default | Description |
|---|---|---|
| `enabled` | `true` | Turns memory on/off entirely |
| `provider` | `null` | Active provider id — `"local"`, `"mem0"`, `"supermemory"`, or `null` to disable |
| `sync_turns` | `true` | Extract and store facts from each completed turn |
| `prefetch` | `true` | Inject recalled memories before each turn |
| `max_prompt_chars` | `6000` | Cap on recalled memory injected into the prompt |

External providers need their SDK installed before selecting them:

```bash
uv pip install ".[memory]"
```

## LocalMemoryAPI

The built-in provider. No external service and no API key. Semantic search uses
`fastembed` (a local, CPU-only ONNX embedder) which ships as a core dependency,
so semantic recall is on by default. If the embedder ever fails to load,
retrieval degrades gracefully to keyword overlap.

**Storage** — JSONL at `profile_dir/memory/memories.jsonl`. Each line is a JSON object:

```json
{"id": "<uuid hex>", "content": "...", "source": "turn|manual|compact", "created_at": "...", "created_ts": 1234567890.0}
```

Embeddings are cached in a sidecar `profile_dir/memory/vectors.json` (keyed by
entry id), so the JSONL stays human-readable and facts are never re-embedded on
restart. Entries written before `fastembed` was present are embedded lazily on
the first semantic search.

**Fact extraction** — On `on_turn_complete`, the agent LLM is called with a short extraction prompt asking for durable facts from the turn. The response is split by line; `NONE` is treated as no facts worth storing.

**Search** — When `fastembed` is available, ranks by embedding cosine similarity
blended with a recency bias; otherwise falls back to keyword overlap (Jaccard).
The recency blend is identical either way:

```
score = relevance × 0.7 + recency × 0.3   # relevance = cosine sim (or Jaccard overlap in fallback)
recency = 1 / (1 + age_days / 30)          # half-weight at 30 days
```

In semantic mode, matches below a `min_relevance` floor (default `0.5`) are
discarded, so an unrelated query returns nothing rather than weak noise. Both
`min_relevance` and the embedding model (`embed_model`, default
`BAAI/bge-small-en-v1.5`) can be set via the provider's `MemoryOptions.config`.

**Prefetch** — Top-5 matches by score are formatted as a `## Recalled Memory` block and injected before each turn.

Explicit search/store/forget is available through the provider-agnostic `memory`
tool (see [Memory tool](#memory-tool)); `LocalMemoryAPI` does not expose its own
per-provider tool schemas.

## HindsightMemoryAPI

Optional adapter for [Hindsight](https://hindsight.vectorize.io) (Vectorize), built
on the `hindsight_client.Hindsight` HTTP client. It talks to a Hindsight server —
the managed cloud (`https://api.hindsight.vectorize.io`) or a local/Docker instance
via `HINDSIGHT_API_URL`. Memories are scoped to a single Hindsight **bank**
(`bank_id`, default `operator`).

It maps Hindsight's three core operations onto the provider interface:

| Hindsight op | Provider hook | Purpose |
|---|---|---|
| `recall` | `search`, `prefetch` (default) | Retrieve raw stored facts |
| `retain` | `remember`, `on_turn_complete` | Store facts / sync turns |
| `reflect` | `reflect`, `prefetch` (when `prefetch_method="reflect"`) | LLM-synthesized cross-memory answer |

**Config** (via the provider's `MemoryOptions.config`, env vars take priority):

| Key | Env | Default | Description |
|---|---|---|---|
| `bank_id` | `HINDSIGHT_BANK_ID` | `operator` | Memory bank scope |
| `api_url` | `HINDSIGHT_API_URL` | `https://api.hindsight.vectorize.io` | Server URL (cloud or local) |
| `api_key_env` | `HINDSIGHT_API_KEY` | — | Bearer token (required for cloud) |
| `budget` | `HINDSIGHT_RECALL_BUDGET` | `mid` | Recall/reflect budget: `low`/`mid`/`high` |
| `prefetch_method` | — | `recall` | `recall` (raw facts) or `reflect` (synthesized) |
| `recall_max_tokens` | — | `null` | Cap on recalled tokens |

**`reflect`** is Hindsight's distinguishing capability — synthesis across stored
memories rather than raw retrieval. It is surfaced provider-agnostically: the
`memory` tool gains a `reflect` action and `MemoryManager.reflect()` routes to the
active provider. Other providers return empty (unsupported) by default.

Select it in the profile's `settings.json`:

```json
{ "memory": { "provider": "hindsight" } }
```

```bash
uv pip install ".[memory]"
export HINDSIGHT_API_KEY=hsk_...
```

## HolographicMemoryAPI

Self-contained local provider modeled on Hermes's Holographic store — **no
external service, no API key, no network**. Facts are kept in a SQLite database
with an FTS5 full-text index, and each fact carries a **trust score** in `[0, 1]`.

**Storage** — SQLite at `profile_dir/memory/holographic.db` (override with the
`db_path` config key). Two tables: `facts` (content, source, session, timestamp,
trust, hits) and an `facts_fts` FTS5 index.

**Ranking** — full-text relevance blended with trust and recency:

```
score = relevance × 0.5 + trust × 0.3 + recency × 0.2
```

If the FTS5 query errors, search falls back to a `LIKE` substring scan over the
most recent facts.

**Self-evolving trust** — facts returned by a recall get a small trust bump
(`+0.02`, capped at 1.0) and an incremented hit count, so facts that keep proving
useful across sessions rise in the ranking. (Explicit helpful/unhelpful feedback
tooling is not surfaced; trust trains implicitly through recall.)

**Config** (`MemoryOptions.config`): `default_trust` (default `0.5`), `db_path`.

> Note: this implements the substantive parts of Holographic (FTS5 store + trust
> scoring + recency). True HRR (Holographic Reduced Representation) compositional
> vector algebra is **not** implemented.

```json
{ "memory": { "provider": "holographic" } }
```

## OpenVikingMemoryAPI

Optional adapter for [OpenViking](https://github.com/volcengine/OpenViking), a
filesystem-paradigm context database with **tiered loading** (L0 summary → L1
overview → L2 full). Prefetch and search return the light L0/L1 tiers so recalled
context stays token-cheap; full L2 detail is only read on demand.

Connects to a running OpenViking server when `OPENVIKING_ENDPOINT` (or the
`endpoint` config key) is set, otherwise runs embedded against a data directory
under the active profile (`profile_dir/memory/openviking`).

| Hook | OpenViking op |
|---|---|
| `search` / `prefetch` | `find` → L0/L1 summaries of matched resources |
| `remember` / `on_turn_complete` | `remember` (falls back to `write` / `add_resource`) |

**Config**: `endpoint` (`OPENVIKING_ENDPOINT`), `api_key_env`
(`OPENVIKING_API_KEY`), `target_uri` (default `viking://memory/`).

```bash
uv pip install ".[memory]"
export OPENVIKING_ENDPOINT=http://localhost:1933
```

```json
{ "memory": { "provider": "openviking" } }
```

## Consolidation workflow

`MemoryConsolidateWorkflow` (`memory/workflows/consolidate.py`) is an internal workflow — not exposed to users via the `workflow` tool. Run it periodically to keep the store clean.

**Invocation:**

```python
from operator_use.memory.workflows.consolidate import MemoryConsolidateWorkflow
from operator_use.workflow.types import WorkflowInvocation

await MemoryConsolidateWorkflow().execute(
    WorkflowInvocation(
        workflow_name='memory-consolidate',
        args={'store_path': str(profile.memory_dir / 'memories.jsonl')},
    ),
    workflow_context,
)
```

**What it does:**
1. Loads all entries from the JSONL store.
2. Sends the full list to an agent with instructions to merge duplicates, remove superseded facts, and resolve contradictions.
3. Agent returns a JSON array of surviving entries.
4. Rewrites the JSONL in place with the consolidated result; adds a `consolidated_at` timestamp to each entry.

## Lifecycle hooks

`MemoryManager` forwards these hooks to the active provider when one is configured:

| Hook | Trigger | Purpose |
|---|---|---|
| `prefetch(query)` | Before each turn | Return recalled context block |
| `queue_prefetch(query)` | After a turn | Warm retrieval for the next turn |
| `on_turn_complete(user, assistant)` | After each completed turn | Extract and persist durable facts |
| `on_session_end(messages)` | Session shutdown | Final flush or extraction pass |
| `on_pre_compact(messages)` | Before compaction | Preserve facts before context is discarded |
| `on_memory_write(action, target, content)` | When `memory` tool writes | Mirror explicit writes into the store |
| `shutdown()` | Runtime shutdown | Close provider resources |

## Memory tool

The `memory` builtin tool is provider-agnostic. It routes through `MemoryManager`:

| Action | Required field | Purpose |
|---|---|---|
| `search` | `query` | Retrieve relevant memories from the active provider |
| `remember` | `content` | Store a durable fact |
| `forget` | `memory_id` | Remove a memory by ID |
| `reflect` | `query` | Synthesize an answer across stored memories (provider-dependent; Hindsight) |

## Custom providers via extensions

```python
from operator_use.memory.provider.types import MemoryProvider
from operator_use.memory.api.base import BaseMemoryAPI
from operator_use.memory.types import MemoryOptions

class MyMemoryAPI(BaseMemoryAPI):
    async def prefetch(self, query, *, session_id="") -> str:
        return "recalled context for: " + query

    async def on_turn_complete(self, user_content, assistant_content, *, session_id=""):
        pass  # persist the turn

def extension(api):
    api.register_memory_provider(MemoryProvider(
        id="my-memory",
        name="My Memory",
        api="my_memory_api",
        options=MemoryOptions(),
    ))
    api.register_memory_api("my_memory_api", MyMemoryAPI)
```

Select it in the profile's `settings.json`:

```json
{ "memory": { "provider": "my-memory" } }
```

See [extensions.md](./extensions.md) for the full provider registration pattern.

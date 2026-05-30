# Knowledge base

Knowledge documents are markdown files that Operator injects into the system prompt. Documents can be pre-loaded directly into the prompt or listed as available for the agent to read on demand. The `knowledge` builtin tool provides agent-facing actions for querying, adding, and maintaining knowledge pages.

## Architecture

```
operator_use/knowledge/
  service.py          ← Knowledge class — index loading, discovery, system-prompt injection
  prompts.py          ← prompt templates used by knowledge workflows
  workflows/
    ingest.py         ← KnowledgeIngestWorkflow
    query.py          ← KnowledgeQueryWorkflow
    lint.py           ← KnowledgeLintWorkflow
    consolidate.py    ← KnowledgeConsolidateWorkflow
```

## Index file (preferred)

Each profile's `knowledge/` directory can contain an `index.yaml` that explicitly declares which documents are available and how they are handled:

```yaml
# ~/.operator/profiles/<name>/knowledge/index.yaml

- path: company/overview.md
  always_load: true          # inject content directly into the prompt
  priority: high
  tags: [company, overview]

- path: api/reference.md
  always_load: false         # list as available for on-demand read (default)
  priority: normal
  tags: [api, reference]
```

### Fields

| Field | Required | Default | Description |
|---|---|---|---|
| `path` | Yes | — | Path relative to the `knowledge/` directory |
| `always_load` | No | `false` | `true` → inject full content into prompt; `false` → list as available |
| `priority` | No | `normal` | Agent hint: `high` \| `normal` \| `low` |
| `tags` | No | `[]` | Topic labels, comma-joined in the prompt |

An empty or comment-only `index.yaml` is valid — treated as no entries defined yet.

### Bootstrap

`bootstrap_profile()` creates a commented `knowledge/index.yaml` template in every new profile directory. Existing profiles without one fall back to filesystem scan.

## Filesystem scan (fallback)

If no `index.yaml` is present, `Knowledge` scans the directory for markdown files. Two layouts are supported simultaneously:

### Directory nodes (preferred for multi-file topics)

```
knowledge/products/index.md     → name "products"
knowledge/api/v2/index.md       → name "api/v2"
```

A directory with an `index.md` becomes a single named entry.

### Flat files

```
knowledge/company.md            → name "company"
knowledge/pricing.md            → name "pricing"
```

Single `.md` files (not named `index.md`) become named entries. All discovered files are treated as on-demand.

## System prompt injection

Knowledge is injected via `append_system_prompt` by `ResourceLoader._reload_append_system_prompt()`.

### With `index.yaml`

Documents with `always_load: true` are injected as `<knowledge>` blocks with full content. Remaining documents are listed in an `<available_knowledge>` block:

```
## Knowledge

<knowledge path="company/overview.md">
...full file content...
</knowledge>

<available_knowledge>
  <doc path="api/reference.md" priority="normal" tags="api,reference" />
</available_knowledge>

Use the `read` tool to load any available knowledge document when needed.
```

### Without `index.yaml` (filesystem scan fallback)

All discovered documents are listed as available:

```
## Knowledge

<available_knowledge>
  <doc path="company.md" priority="normal" description="Acme Corp overview and history" />
  <doc path="api/reference.md" priority="normal" />
</available_knowledge>

Use the `read` tool to load any available knowledge document when needed.
```

## Precedence

When multiple knowledge directories are passed to `Knowledge(*dirs)`, entries are deduplicated by path — the first directory (highest priority) wins.

## Adding knowledge documents

**Profile knowledge** (active when the profile is running):

```
~/.operator/profiles/<name>/knowledge/
  index.yaml          ← declare entries here
  company/
    overview.md
  api/
    reference.md
```

## Knowledge tool

The `knowledge` builtin tool (available when an active profile with a `knowledge/` directory exists) exposes knowledge operations directly to the agent.

### Actions

| Action | Required fields | Description |
|---|---|---|
| `list` | — | List all knowledge pages with a one-line preview and absolute path |
| `query` | `query` | Answer a question using only pages in the knowledge base |
| `add` | `content`, `page` (opt) | Append text directly to a knowledge page without any intermediate step |
| `ingest` | `source` | Synthesize a source into knowledge pages |
| `lint` | — | Check pages for contradictions and stale content |
| `consolidate` | — | Consolidate and deduplicate all pages |
| `log` | — | Return the audit log (`log.md`) |

### Ingest sources

The `ingest` action auto-detects the source type:

| Source | Detection rule | Behaviour |
|---|---|---|
| YouTube URL | `youtube.com/` or `youtu.be/` in URL | Fetches metadata via `yt-dlp`; fetches transcript via `youtube-transcript-api` |
| HTTP/S URL | Starts with `http://` or `https://` | Fetches page via `httpx`; converts HTML to Markdown via `markdownify` |
| File path | Starts with `/`, `./`, `../`, `~`, or path exists on disk | Reads file directly from disk |
| Raw text | Anything else | Content is used directly — no fetch step |
| Unknown type | Explicit `source_type` not matching any above | Delegated to a sub-agent with general tools (`web_search`, `web_fetch`, `read`, `write`, `edit`) |

Dependencies for YouTube ingest: `yt-dlp` and `youtube-transcript-api` must be installed.
Dependencies for URL ingest: `httpx` and `markdownify` (both included in the default install).

### Knowledge workflows

All multi-step operations run as structured `Workflow` subclasses in `operator_use/knowledge/workflows/`. They are invoked inline (not as background tasks) when called from the `knowledge` tool.

| Workflow class | `workflow_name` | Phases |
|---|---|---|
| `KnowledgeIngestWorkflow` | `knowledge-ingest` | read → synthesize → index |
| `KnowledgeQueryWorkflow` | `knowledge-query` | answer |
| `KnowledgeLintWorkflow` | `knowledge-lint` | scan → report |
| `KnowledgeConsolidateWorkflow` | `knowledge-consolidate` | read → consolidate → index |

### Prompts

All LLM prompt strings used by the knowledge workflows are centralised in `operator_use/knowledge/prompts.py`:

| Function | Used by |
|---|---|
| `ingest_fallback_read()` | `KnowledgeIngestWorkflow` — sub-agent fallback for unknown source types |
| `ingest_synthesize()` | `KnowledgeIngestWorkflow` — synthesize phase |
| `ingest_index()` | `KnowledgeIngestWorkflow` — index phase |
| `query_answer()` | `KnowledgeQueryWorkflow` |
| `lint_scan()` | `KnowledgeLintWorkflow` — scan phase |
| `lint_report()` | `KnowledgeLintWorkflow` — report phase |
| `consolidate_overview()` | `KnowledgeConsolidateWorkflow` — read phase |
| `consolidate_run()` | `KnowledgeConsolidateWorkflow` — consolidate phase |
| `consolidate_index()` | `KnowledgeConsolidateWorkflow` — index phase |

## Wiki command

`/wiki` is the interactive alias for knowledge base management:

```
/wiki ingest <source>    — ingest a URL, file, or YouTube link
/wiki query <question>   — answer a question from the knowledge base
/wiki lint               — check for contradictions and stale content
/wiki consolidate        — consolidate and deduplicate all pages (background)
/wiki log                — show the audit log
```

The `consolidate` subcommand runs as a background workflow via `WorkflowManager`.

## Related documents

- [docs/profiles.md](./profiles.md) — Profile resource paths
- [docs/skill.md](./skill.md) — Skills — similar discovery mechanism for behavioural guidance
- [docs/agent.md](./agent.md) — System prompt construction
- [docs/workflow.md](./workflow.md) — Workflow base class and execution model
- [docs/memory.md](./memory.md) — Agent memory (separate from knowledge documents)

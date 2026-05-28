# Knowledge base

Knowledge documents are markdown files that Operator injects into the system prompt. Documents can be pre-loaded directly into the prompt or listed as available for the agent to read on demand.

## Architecture

```
operator_use/knowledge/
  service.py    ← Knowledge class — index loading, discovery, system-prompt injection
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

## Related documents

- [docs/profiles.md](./profiles.md) — Profile resource paths
- [docs/skill.md](./skill.md) — Skills — similar discovery mechanism for behavioural guidance
- [docs/agent.md](./agent.md) — System prompt construction

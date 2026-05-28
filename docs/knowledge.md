# Knowledge base

Knowledge documents are markdown files that Operator discovers at startup and injects as a compact index into the system prompt. The agent can load any document on demand.

## Architecture

```
operator_use/knowledge/
  service.py    ← Knowledge class — discovery, deduplication, system-prompt injection
```

The `Knowledge` class scans one or more directories, deduplicates entries by name (highest-priority directory wins), and produces a formatted index string for the system prompt.

## Discovery layout

Two supported layouts — both work simultaneously within the same directory:

### Directory nodes (preferred for multi-file topics)

```
knowledge/products/index.md     → name "products"
knowledge/api/v2/index.md       → name "api/v2"
```

A directory with an `index.md` becomes a single named entry. The index file is the primary document; other files in the directory are support files.

### Flat files

```
knowledge/company.md            → name "company"
knowledge/pricing.md            → name "pricing"
```

Single `.md` files (not named `index.md`) become named entries.

## Precedence

Discovery paths are ordered from lowest to highest priority. When the same name appears in multiple directories, the highest-priority directory wins.

Default scan order:
1. Global: `~/.operator/knowledge/`
2. Project: `<project>/.operator/knowledge/`
3. Profile: `~/.operator/profiles/<name>/knowledge/`

Profile and project docs override global docs with the same name.

## System prompt injection

If any knowledge documents are found, a compact index is appended to the system prompt:

```
## Knowledge base

- **company** — Acme Corp overview and history
- **products** — Product catalog and specifications
- **api/v2** — REST API v2 reference
```

Each entry shows the document name and a one-line preview (first non-empty line of the document, up to 120 characters).

The agent can load the full content of any document on demand using the `read` tool with the absolute path, or by asking Operator to surface the knowledge inline.

## Adding knowledge documents

**Global knowledge** (available in every session):

```
~/.operator/knowledge/
  company.md
  products/
    index.md
    catalog.csv     ← support file (not auto-discovered as a top-level entry)
```

**Project knowledge** (available when `cwd` is inside the project):

```
<project>/.operator/knowledge/
  architecture.md
  api/index.md
```

**Profile knowledge** (available when the profile is active):

```
~/.operator/profiles/coder/knowledge/
  coding-guidelines.md
```

## Related documents

- [docs/profiles.md](./profiles.md) — Profile resource paths
- [docs/skill.md](./skill.md) — Skills — similar injection mechanism for behavioural guidance
- [docs/agent.md](./agent.md) — System prompt construction

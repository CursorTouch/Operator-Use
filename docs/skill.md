# Skills

Skills are markdown files that inject context and instructions into the system prompt. Each skill is a directory containing a `SKILL.md` file with a YAML frontmatter block describing the skill and a body that becomes an inline block in the system prompt.

## Skill file format

```
my-skill/
  SKILL.md
```

`SKILL.md` structure:

```markdown
---
name: my-skill
description: Teaches the agent how to do X.
disable-model-invocation: false
---

# My Skill

When working on X, always do Y.
...
```

**`name`** — must match the parent directory name. Lowercase letters, digits, and hyphens only. Max 64 characters. Cannot start or end with a hyphen or contain consecutive hyphens.

**`description`** — required. Shown when listing available skills. Max 1024 characters.

**`disable-model-invocation`** — optional boolean. When `true`, the agent does not call the LLM for turns associated with this skill. Default: `false`.

If `description` is missing or empty, the skill is not loaded (the file is silently skipped with a warning diagnostic).

## Discovery order

`ResourceLoader` drives skill discovery. On `reload()` it scans four sources in priority order:

1. **Built-in skills** — `program/builtins/skills/` (source: `"builtin"`)
2. **User skills** — `~/.program/agent/skills/` (source: `"user"`)
3. **Project skills** — `<project>/.program/agent/skills/` (source: `"project"`)
4. **Explicit paths** — entries in `ResourceLoaderOptions.additional_skill_paths`, resolved relative to cwd (source: `"path"`)

Within each source, directories are scanned recursively. If a directory contains a `SKILL.md` at its root, it is treated as a single skill and subdirectories are not scanned further.

**Name collision**: if two skills from different sources share a name, the first-loaded wins and a `collision` diagnostic is emitted. The winner is determined by source order (user → project → path) and then alphabetical order within a source.

**Symlink deduplication**: resolved real paths are tracked. If the same file is encountered twice (e.g., via a symlink), the duplicate is silently skipped.

Extensions can also contribute skill paths by returning `ResourcesDiscoverResult(skill_paths=[...])` from a `resources_discover` handler.

## Skill data model

```python
class Skill(BaseModel):
    name: str
    description: str
    file_path: Path        # absolute path to SKILL.md
    base_dir: Path         # parent directory of SKILL.md
    source_info: SourceInfo
    disable_model_invocation: bool = False
```

```python
class SourceInfo(BaseModel):
    path: str
    source: str            # "user", "project", or "path"
    scope: str | None      # "user" | "project" | None
    base_dir: str | None
```

## Diagnostics

Every load problem emits a `ResourceDiagnostic` rather than raising:

```python
class ResourceDiagnostic(BaseModel):
    type: Literal['warning', 'collision', 'error']
    message: str
    path: str
    collision: CollisionInfo | None = None
```

`CollisionInfo` carries `resource_type`, `name`, `winner_path`, and `loser_path` so the caller can surface the conflict clearly.

## System prompt injection

The `ResourceLoader` hands skill objects to `PromptTemplate.build()`. Skills are injected as named blocks inside the system prompt — the LLM sees the skill body and description as an instruction section. Skills marked `disable_model_invocation=True` are flagged to the engine so the model call can be skipped.

## Validation rules

| Rule | Error |
|---|---|
| `name` must match parent directory name | `name "X" does not match parent directory "Y"` |
| `name` max 64 chars | `name exceeds 64 characters` |
| `name` must be `[a-z0-9-]+` | `name contains invalid characters` |
| `name` must not start/end with `-` | `name must not start or end with a hyphen` |
| `name` must not contain `--` | `name must not contain consecutive hyphens` |
| `description` is required | `description is required` |
| `description` max 1024 chars | `description exceeds 1024 characters` |

Validation errors become `warning` diagnostics. A skill with a missing description is not loaded; a skill with a name mismatch is still loaded (with a warning).

## Related documents

- [extensions.md](./extensions.md) — How extensions contribute additional skill paths via `resources_discover`
- [agent.md](./agent.md) — How `_rebuild_system_prompt()` uses skills

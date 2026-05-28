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

`ResourceLoader` drives skill discovery. On `reload()` it scans sources in this priority order:

| Source | Directory | Notes |
|---|---|---|
| Built-in | `operator_use/builtins/skills/` | Always present |
| Profile | `~/.operator/profiles/<name>/skills/` | Only when `--profile <name>` is active |
| Extensions | paths from `resources_discover` handlers | Registered at load time |
| Packages | paths from installed packages | Registered at load time |

Path helpers are defined in `operator_use/settings/paths.py`.

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

The `ResourceLoader` hands skill objects to `PromptTemplate.build()` via `format_skills_for_prompt()`. The injected block uses strong enforcement language to ensure the agent checks skills before acting:

```
# Skills — FIRST PRIORITY (MANDATORY)

CRITICAL: You have installed skills that provide specialized capabilities.
Before attempting ANY task — simple or complex — you MUST check if an installed skill handles it.

## Rules (MUST follow in order)
1. ALWAYS scan the skill list below BEFORE taking ANY action on a user request
2. If a skill's description matches or partially matches the task, you MUST load its full
   instructions using the `skill` tool: `skill action="view" name="<name>"` — do this BEFORE anything else
3. Follow the loaded skill instructions EXACTLY — do NOT improvise or use alternative approaches
4. NEVER use general-purpose workarounds when a skill provides the right tool
5. If multiple skills could apply, load the most specific one first
6. Even for seemingly simple tasks, CHECK SKILLS FIRST

## Enforcement
- If you skip checking skills and use a raw approach for a task that a skill handles,
  this is considered a FAILURE. Always check skills first.

<available_skills>
  <skill>
    <name>skill-name</name>
    <description>skill description</description>
    <location>/absolute/path/to/profiles/<name>/skills/skill-name/SKILL.md</location>
  </skill>
</available_skills>

To load a skill's full instructions: `skill action="view" name="<name>"`
Scripts within a skill are relative to the skill's directory (parent of SKILL.md).
```

The `<location>` field carries the absolute path to `SKILL.md` from `Skill.file_path`, so the agent always knows where to find the file without guessing. Skills marked `disable_model_invocation=True` are excluded from the injected block entirely.

## Skill tool

The `skill` builtin tool lets the agent manage skills at runtime. All actions
target skills by name.

| Action | Required params | Description |
|---|---|---|
| `view` | `name` | Read the full SKILL.md content |
| `create` | `name`, `description`, `body` | Create a new skill directory + SKILL.md |
| `edit` | `name`, `content` | Replace the entire SKILL.md |
| `patch` | `name` | Update frontmatter fields only (description, disable-model-invocation) |
| `delete` | `name` | Remove the skill directory and all its files |
| `write_file` | `name`, `filename`, `content` | Write a file inside the skill directory |
| `remove_file` | `name`, `filename` | Remove a file from inside the skill directory |

Skills are written to the active profile's skill directory
(`~/.operator/profiles/<name>/skills/`). **Write and delete actions require an
active profile** — if the agent is started without `--profile`, those actions
return an error. Read actions (`list`, `view`) always work; they include builtins
and any profile skills if a profile is active.

After any write action the resource loader reloads skills automatically.

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
- [auth.md](./auth.md) — Profile directory layout (`~/.operator/profiles/<name>/`)

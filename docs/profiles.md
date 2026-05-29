# Agent profiles

Named agent profiles give the agent its own identity, system prompt, model/provider override, tool allowlist, and isolated resource directories — all defined by a single `AGENT.md` file.

## Discovery

Profiles are discovered from:

```
~/.operator/profiles/<name>/AGENT.md
```

On startup with `--profile <name>`, the runtime loads the matching profile. Subagents can also be spawned against any named profile via the `subagent` or `peer_agent` tools.

## AGENT.md format

`AGENT.md` serves two purposes: the frontmatter configures the profile (model, tools, etc.) and the body is the **operation manual** — instructions for how the agent behaves in this profile. Identity and persona live in `SOUL.md`; stable user context lives in `USER.md`. The body governs scope, memory, style, safety, and task execution.

```markdown
---
name: coder
description: Senior software engineer focused on Python and TypeScript.
model: claude-opus-4-7
provider: anthropic
tools: read, edit, write, grep, glob, terminal
---

# Operation Manual

## Scope Discipline
Implement exactly what is requested. State assumptions before acting.
Do not add unrequested refactors or features.

## Writing Style
Precise and minimal. No filler phrases. Match the developer's register.

## Memory
Update MEMORY.md immediately when you learn something new about the project
or when corrected. Keep it under 3000 characters.
```

### Frontmatter fields

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Profile name (lowercase, max 64 chars) |
| `description` | No | One-line description of the agent's role |
| `model` | No | LLM model override, e.g. `claude-opus-4-7` |
| `provider` | No | LLM provider override, e.g. `anthropic` |
| `tools` | No | Comma-separated tool allowlist; empty = all tools |

The body becomes the custom system prompt. An empty body means "use the default system prompt." A template for a complete operation manual is at `operator_use/templates/AGENT.md`.

## Per-profile resource layout

Each profile has its own resource directory under `~/.operator/profiles/<name>/`:

```
~/.operator/profiles/<name>/
  AGENT.md          ← profile definition
  settings.json     ← per-profile settings overlay (merged over global)
  SOUL.md           ← persona override (injected into system prompt)
  USER.md           ← user profile override
  MEMORY.md         ← memory context override
  sessions/         ← session JSONL files
  tools/            ← per-profile custom tools
  skills/           ← per-profile skills
  extensions/       ← per-profile extensions
  commands/         ← per-profile slash commands
  hooks/            ← per-profile hooks
  subagents/        ← per-profile subagent templates
  knowledge/        ← per-profile knowledge documents
    index.yaml      ← knowledge manifest (always_load, tags, priority per document)
  workflows/        ← per-profile workflow files
  teams/            ← team state (TeamManager)
  acp/              ← ACP session bookmarks
  peer/             ← peer agent session bookmarks
  temp/             ← scratch files (cleared on restart)
  crons.json        ← scheduled cron jobs
```

Resource loading order (highest priority last):
1. Builtins
2. Global (`~/.operator/agent/`)
3. Project (`<project>/.operator/agent/`)
4. Profile (`~/.operator/profiles/<name>/`)

## System prompt profile block

When a profile is active, `PromptTemplate` injects a `## Profile` block into the system prompt that tells the agent where its key files live:

```
## Profile: ~/.operator/profiles/<name>

- <profile>/SOUL.md        — your persona and identity (defines who you are, your tone, operating principles, and constraints)
- <profile>/MEMORY.md      — long-term memory; read at the start of sessions, write here to persist things across conversations
- <profile>/USER.md        — who you are talking to: name, timezone, technical background, communication preferences, and things to avoid
- <profile>/skills         — skill guides, each as {name}/SKILL.md; scan before tasks and load with skill action="view" when relevant
- <profile>/knowledge      — user-curated reference material (domain docs, API specs, guidelines, company knowledge); declared in index.yaml — read the relevant file(s) with the read tool when the task requires background context not in the conversation
- <profile>/workflows      — reusable Python workflow scripts; each has a name and description — run with the workflow tool action="run"
- <profile>/temp           — scratchpad and working files; use as terminal CWD for intermediate output
```

## Settings overlay

`settings.json` inside a profile directory is merged on top of the global settings. Fields not present in the profile overlay inherit the global value.

```json
{
  "default_model": "claude-opus-4-7",
  "browser_use_enabled": true,
  "compaction": { "reserve_tokens": 32768 }
}
```

## Starting with a profile

```bash
operator --profile coder --cwd /my/project
```

Programmatically:

```python
from operator_use.agent.profile import load_agent_profiles
from operator_use.settings.paths import get_profiles_dir
from operator_use.runtime.types import RuntimeConfig

result = load_agent_profiles(get_profiles_dir())
profile = next(p for p in result.profiles if p.name == "coder")

config = RuntimeConfig(cwd="/my/project", profile=profile)
```

## Ephemeral profiles

Subagents that don't match a named profile get an ephemeral profile — an auto-generated temporary directory that is deleted when the process exits.

```python
from operator_use.agent.profile import create_ephemeral_profile

profile = create_ephemeral_profile()
# profile.profile_dir is a tempdir; deleted via atexit hook
```

Ephemeral profiles have:
- No `AGENT.md` (generated internally)
- No persistent sessions
- No team/peer state
- ACP/cron/sessions are in-memory only

## Profile operations requiring an active profile

Some features only work with a durable named profile. Without `--profile`, these operate in-memory only (no persistence):

- `ACPSessionManager` — session bookmarks
- `PeerSessionManager` — peer bookmarks
- `TeamManager` — team state
- `CronScheduler` — cron persistence
- Subagent session history

## Tool allowlist

When the `tools` frontmatter field is set, the agent only sees those tools. Builtins not in the list are hidden from the LLM. Extensions can still add tools that bypass the allowlist unless the extension is also disabled.

## Related documents

- [docs/session.md](./session.md) — Session persistence and profile-scoped paths
- [docs/acp.md](./acp.md) — ACP session persistence requiring a profile
- [docs/team.md](./team.md) — Team state and profile requirement
- [docs/extensions.md](./extensions.md) — Extension loading order including profiles

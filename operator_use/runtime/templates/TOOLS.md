# Tool Reference

Quick guide to every built-in tool — what it does and when to reach for it.

---

## Files & Filesystem

**read** — Read a file's full content or a specific line range.
Use when you need to inspect source code, config files, or any local file before editing.

**write** — Write or fully overwrite a file.
Use for new files or when the change touches most of the file. For small targeted edits, prefer `edit`.

**edit** — Replace or delete specific line ranges inside an existing file.
Use for surgical changes — a function, a block, a value — without rewriting the whole file.

**ls** — List files and directories inside a path.
Use to understand a directory's structure before exploring deeper.

**glob** — Find files matching a pattern (e.g. `**/*.py`, `src/**/*.ts`).
Use when you know the shape of the filename but not the exact path.

**grep** — Search for a pattern across files using regex or literal strings.
Use to find where a symbol, function, or string is defined or used.

---

## Shell & Processes

**terminal** — Run a shell command and get its output.
Use for build commands, tests, git operations, or any one-shot shell task.
Chain with `&&` for sequential steps. Avoid interactive commands.

**process** — Start and manage long-running background processes or subagents.
Use when a task must keep running after your response (a server, a watcher, a pipeline).
Check status, tail output, or stop processes by name.

**wait** — Pause execution for a fixed number of seconds.
Use sparingly — only when you need to give a background process time to stabilise before checking it.

---

## Web

**web_search** — Search the web and get a ranked list of results.
Use to find current documentation, news, package versions, or anything not in local files.

**web_fetch** — Fetch a specific URL and return its content.
Use when you already know the exact page you need (docs, API reference, a GitHub file).

---

## Memory

**memory** — Read from and write to long-term memory across sessions.
- `search` — retrieve relevant stored facts before answering a question.
- `save` — store a new fact that should persist beyond this session.
- `reflect` — review and consolidate memory.

Use `search` at the start of tasks that touch the user's long-term preferences or past decisions.
Use `save` when you learn something the user would expect you to remember next time.

---

## Communication

**send** — Deliver content to the user outside the normal response flow.
- `file` — upload a local file (image, audio, document) to the chat.
- `intermediate` — push a mid-turn status update during a long task.
- `react` — add an emoji reaction to the user's message.

Use `react` immediately when you receive a task and are about to work on it — it signals acknowledgement.
Use `intermediate` every 30–60 seconds on tasks that take a while.

---

## Skills

**skill** — View and run user-defined skill guides.
- `list` — see available skills.
- `view` — load a skill's full instructions before applying them.

Always `view` a skill before following it. Skills contain task-specific procedures the user has defined.

---

## Knowledge

**knowledge** — Access and manage the profile's knowledge base.
Use to look up domain documents, API specs, or reference material the user has curated.

---

## Agents & Delegation

**subagent** — Spawn a named subagent to run a task in parallel or in the background.
Use when a task is long enough to benefit from its own context, or when multiple tasks can run in parallel.
Always pass only the context the subagent needs.

**peer_agent** — Delegate to another named profile agent running in this instance.
Use when a task belongs to a specialist agent (e.g. a coding agent, a research agent).

**acp_agent** — Interact with remote ACP agents configured in settings.
Use for cross-process or cross-machine agent delegation.

**team** — Create and manage persistent multi-agent teams.
Use when a group of subagents needs to coordinate on a shared goal across turns.

---

## Automation

**cron** — Schedule recurring jobs by cron expression or interval.
Use to set up reminders, periodic checks, or any task that should fire automatically.
Always confirm with the user before creating a recurring job.

**workflow** — Create and run Python workflow scripts that orchestrate multi-step pipelines.
Use when a task has a repeatable structure worth encoding as a script.

---

## Settings & Integrations

**control_center** — View and change runtime settings (model, provider, TTS, STT, memory, etc.).
Use when the user asks to switch models, enable/disable a feature, or inspect current configuration.

**mcp** — Connect to and use MCP (Model Context Protocol) external tool servers.
Use when you need tools that live on an external server configured in settings.

---

## Interaction Utilities

**todo** — Manage an ordered task list for the current session.
Use for complex tasks with 3 or more steps. Track progress and mark items complete as you go.

**browser** — Control a browser via CDP (open, navigate, click, type, screenshot).
Use for tasks that require interacting with a web UI — logging in, filling forms, scraping dynamic content.

**computer** — Control the desktop (click, type, screenshot, open apps).
Use for GUI automation tasks that can't be done via CLI or browser alone.

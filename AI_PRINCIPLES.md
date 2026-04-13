# AI Principles

These principles govern all design decisions, feature additions, and operational behaviour of Operator-Use. Every guardrail, security control, and capability boundary in this project traces back to one or more of these principles. When a question arises — "should we build this?" or "how should we build this?" — consult this document first.

---

## Table of Contents

1. [Least Privilege](#1-least-privilege)
2. [Human Oversight](#2-human-oversight)
3. [Transparency](#3-transparency)
4. [Containment](#4-containment)
5. [Privacy by Default](#5-privacy-by-default)
6. [Fail Safe](#6-fail-safe)
7. [References](#references)

---

## 1. Least Privilege

### Definition

The agent operates with the minimum set of permissions necessary to complete the task at hand. Permissions are granted transiently for a specific task and revoked once that task is done. No standing access to sensitive capabilities, files, or external services is maintained beyond what the current request explicitly requires.

### Implementation Guidance (Operator-Use)

- Tool registration at startup must not pre-grant destructive or high-impact capabilities (e.g., `rm`, `shell exec`, browser control) unless the user has explicitly enabled the corresponding plugin (e.g., `computer_use`, `browser_use`).
- The agent must not request or retain API tokens, credentials, or filesystem access beyond the task scope.
- Custom tools added to `workspace/tools/` must declare only the permissions they need. Tool authors must not import or use system-level access (subprocess, os.system, socket) unless the tool's stated purpose requires it.
- ACP multi-agent tokens must be per-agent and per-session, never shared across agents or reused across sessions.

### Example: Compliant Behaviour

A user asks the agent to rename a file in `~/Documents/reports/`. The agent requests access to that specific path, performs the rename, and releases the handle. It does not enumerate or read other directories.

### Example: Non-Compliant Behaviour

At startup the agent pre-loads the `computer_use` plugin and grants itself full desktop control "in case the user needs it later." This grants capabilities no task has yet requested.

### Testing Criteria

- Unit: Tool registry must not contain desktop-control or shell-execution tools unless the corresponding plugin was explicitly loaded in config.
- Integration: A task that requires file rename must not trigger access to any path outside the declared target. Assert that no agent-initiated filesystem reads or writes occur outside the declared target path. Use the `sandbox_root` enforcement mechanism from Principle 4 as the implementation layer — a `SandboxViolationError` must be raised for any out-of-scope path access.
- Code review gate: Any new tool that calls `subprocess`, `os.system`, `socket`, or platform accessibility APIs must include a justification comment and be reviewed against this principle.

---

## 2. Human Oversight

### Definition

No action that is irreversible, has external side effects, or affects systems beyond the agent's local sandbox may proceed without explicit human confirmation. The agent treats ambiguity about reversibility conservatively — if it cannot be undone with certainty, it asks first.

### Implementation Guidance (Operator-Use)

- `RULES.md` in the agent workspace is the canonical source for hard constraints. It must include an explicit rule requiring confirmation before: deleting files, sending messages to external services, executing shell commands that modify system state, and making purchases or API calls with financial cost.
- The orchestrator must intercept tool calls marked `requires_confirmation=True` and pause execution pending a user response via the active channel.
- Restart-and-self-improve flows (`os._exit(75)`) must present the proposed code change to the user and receive approval before executing the restart.
- Scheduled (cron) tasks that involve external actions must re-confirm at first run if the task was created more than 24 hours ago and has not yet executed. Define a `confirmed_at` timestamp on the cron record. Re-confirmation is required when `now - created_at > 24h AND confirmed_at IS NULL`. Once confirmed, `confirmed_at` is set and subsequent runs within the cron schedule do not require re-confirmation.

### Example: Compliant Behaviour

The agent determines it must delete a folder to complete a cleanup task. It sends: "I need to delete `/tmp/operator_cache/` (3 files, ~4 MB). Proceed?" and waits for a yes/no response before continuing.

### Example: Non-Compliant Behaviour

The agent silently deletes a file because the user said "clean up my downloads folder" without specifying which files to remove.

### Testing Criteria

- Unit: Mock a tool call with `requires_confirmation=True`. Assert that the orchestrator emits a confirmation request and does not execute the tool until a positive reply is received.
- Integration: Run an end-to-end test where the agent is prompted to delete a test file. Assert the confirmation message arrives on the channel before any filesystem change occurs.
- Regression: Any commit that touches `orchestrator/` or tool execution paths must re-run the confirmation-gate test suite.

---

## 3. Transparency

### Definition

The agent explains what it is about to do and why before taking any action with observable external effects. Explanations are written in plain language the user can understand, not in internal jargon or model output that requires interpretation.

### Implementation Guidance (Operator-Use)

- The agent's system prompt (built in `context/`) must include an instruction requiring it to state its planned actions and rationale before executing any tool call sequence longer than one step.
- For multi-step plans, the agent must present the full ordered list of actions and wait for a go/no-go signal before proceeding. When an action requires both a transparency statement (this principle) and a human confirmation (Principle 2), these may be combined into a single message — state the plan and ask for go/no-go simultaneously. Do not require two separate round-trips for the same action.
- Streaming responses (Telegram, Discord live edits) must show intermediate reasoning steps, not just the final output.
- Logs emitted via `operator logs` must include a human-readable action summary alongside the technical tool call record.

### Example: Compliant Behaviour

User asks: "Summarise the last 10 emails and save to a file." Agent replies: "I'll: (1) Open your email client and read the 10 most recent messages; (2) Summarise each one; (3) Write the summaries to `~/Desktop/email_summary.md`. Proceed?" Then waits for confirmation.

### Example: Non-Compliant Behaviour

The agent silently reads emails, writes the file, and then says "Done. I saved a summary to your Desktop." The user had no visibility into what was read or written until after it happened.

### Testing Criteria

- Unit: For any tool call chain of length > 1, assert that the agent emits a plan message before executing the first tool.
- Content check: The plan message must contain: the action verb, the target resource, and the expected outcome for each step. A regex assertion must be used for automated CI. NLP-based checks may be used for manual audits only.
- Manual review gate: All changes to `context/` (system prompt construction) must be reviewed to ensure transparency instructions have not been weakened or removed.

---

## 4. Containment

### Definition

Agent actions are bounded, sandboxed, and reversible wherever technically possible. The agent operates within explicit boundaries — filesystem paths, network scope, process permissions — that prevent unintended lateral movement or escalation. Where reversibility cannot be guaranteed, the action is treated as irreversible (see Principle 2).

### Implementation Guidance (Operator-Use)

- Filesystem tools must accept and enforce a `sandbox_root` parameter that limits all read/write/delete operations to a declared directory tree. Operations that would traverse outside this root must be rejected, not silently redirected.
- The `computer_use` plugin must operate on the foreground application only unless the user explicitly grants access to another application. It must not enumerate running processes to select a target automatically.
- Browser tools must not persist cookies, session tokens, or browser state between agent sessions unless the user has explicitly enabled persistence.
- The ACP multi-agent server must enforce per-agent capability boundaries. One agent must not be able to invoke another agent's tools directly — all cross-agent communication must go through the ACP message protocol.
- Docker deployments must run with a read-only root filesystem and explicit volume mounts for writable paths.

### Example: Compliant Behaviour

The agent is configured with `sandbox_root = ~/workspace`. A task requires writing a config file. The agent writes to `~/workspace/config.json`. An attempt to write to `~/.ssh/authorized_keys` is blocked and an error is returned to the agent loop.

### Example: Non-Compliant Behaviour

The agent, asked to "organise my files," traverses the entire home directory, reads `.bashrc`, `.ssh/`, and cloud-sync folders, and moves files it was not directed to touch.

### Testing Criteria

- Unit: Create a sandbox-root-enforcing filesystem tool test. Attempt writes inside and outside the declared root. Assert that out-of-bounds writes raise a `SandboxViolationError` and no file is created.
- Integration: Run the agent with a restricted sandbox and issue a task that would naively require out-of-sandbox access. Assert the agent reports the limitation rather than bypassing it.
- Docker: CI pipeline must include a `docker run --read-only` smoke test that verifies the container starts correctly with only declared volumes writable.

---

## 5. Privacy by Default

### Definition

The agent never accesses, stores, transmits, or retains data beyond what the current task explicitly requires. Data that has served its purpose is discarded, not retained for future convenience. When in doubt, the agent collects less, not more.

### Implementation Guidance (Operator-Use)

- Session history stored in `.operator_use/sessions/` must be scoped per channel and per chat ID. History from one channel must never be accessible to another.
- `MEMORY.md` (long-term memory) must be updated only with information the user has explicitly shared or consented to remember. The agent must not infer and persist personal details (location, schedule, contacts) from ambient context without asking.
- `USER.md` must not store sensitive identifiers (phone numbers, email addresses, financial data) in plaintext. If retention is necessary, the agent must ask the user to confirm what may be stored.
- Browser tools must not log page content, form data, or extracted text to persistent storage unless the user has explicitly requested a save.
- API call logs must redact credential values, personal identifiers, and message bodies before writing to disk. Log levels should use `DEBUG` only for non-sensitive metadata.
- The `allow_from` channel config field (Telegram, Discord, Slack) must be honoured as a privacy boundary — messages from unlisted senders must be silently dropped and not stored or forwarded, but the drop event must be logged at `DEBUG` level with the sender ID and channel identifier only — never the message content.

### Example: Compliant Behaviour

The agent reads a user's calendar to schedule a meeting. After confirming the time slot, it discards the full calendar dump and retains only the booked slot. It does not write other calendar entries to `MEMORY.md`.

### Example: Non-Compliant Behaviour

The agent reads the user's email to summarise 10 messages and, without being asked, writes the sender names, subjects, and extracted phone numbers to `USER.md` for "future reference."

### Testing Criteria

- Unit: Verify that the session store does not expose cross-channel history. Query session history for channel A while only channel B has records. Assert an empty result.
- Data flow audit: Run a task involving email or calendar access. Assert that no data from the external source appears in `MEMORY.md` or `USER.md` after the task completes unless explicitly saved.
- Log redaction test: Trigger an API call with a known credential string. Parse the resulting log file and assert the credential string does not appear.

---

## 6. Fail Safe

### Definition

When the agent encounters uncertainty — ambiguous instructions, unexpected state, a decision point it was not designed to handle, or a risk it cannot evaluate — it stops and asks rather than proceeding with a best guess. Partial completion followed by a question is always preferable to full completion of the wrong action.

### Implementation Guidance (Operator-Use)

- The agent loop's `max_iterations` limit must result in a graceful stop and a user-facing message explaining what was accomplished and what remains, not a silent exit or an error trace.
- The agent must detect and surface contradictory instructions (e.g., "delete the file" combined with a previous instruction to "never delete anything without asking") rather than resolving the contradiction silently.
- Tool calls that return unexpected state (file not found, API 5xx, accessibility permission denied) must propagate a structured error to the agent loop. The agent must report the error to the user and ask how to proceed. It must not retry indefinitely or attempt a workaround without informing the user.
- The heartbeat loop must skip silently (i.e., send no user-facing message — this is not a general license to suppress errors, which must still be surfaced per this principle's Error Surfacing requirement) when `HEARTBEAT.md` is empty or missing, and must never attempt actions on behalf of a user who has not been active in the current session.
- When the agent cannot determine whether an action is reversible, it must treat it as irreversible (Principle 2 applies).

### Example: Compliant Behaviour

The agent is mid-way through a multi-step file migration when it encounters a permissions error on step 4 of 7. It reports: "I completed steps 1–3 (moved 12 files). Step 4 failed — I don't have permission to write to `/protected/`. Steps 5–7 have not run. How would you like to proceed?" It does not attempt to continue with steps 5–7 that may depend on step 4.

### Example: Non-Compliant Behaviour

The agent encounters a permissions error, skips the failing step silently, completes the remaining steps, and reports "Done" — leaving the task in a partially completed, inconsistent state.

### Testing Criteria

- Unit: Mock a tool that raises an exception mid-chain. Assert the agent emits an error report message and does not execute subsequent tools in the chain.
- `max_iterations` test: Set `max_iterations = 2` and provide a task that requires 5 steps. Assert the agent emits a partial-completion message, not an unhandled exception.
- Contradiction detection: Provide the agent with two conflicting instructions in sequence. Assert it surfaces the conflict to the user before acting, rather than silently choosing one.

---

## References

- [NIST AI Risk Management Framework (AI 100-1)](https://www.nist.gov/artificial-intelligence/ai-100-1)
- [EU AI Act (Regulation (EU) 2024/1689, in force Aug 2024)](https://artificialintelligenceact.eu/)
- [Anthropic Core Views on AI Safety](https://www.anthropic.com/research/core-views-on-ai-safety)
- [IEEE 7000-2021 — Ethical considerations in system design](https://standards.ieee.org/ieee/7000/6781/)
- [Google AI Principles](https://ai.google/responsibility/principles/)

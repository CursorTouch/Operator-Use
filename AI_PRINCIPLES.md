# AI Safety Principles for Operator-Use

Operator-Use gives an LLM agent real-world capabilities: terminal access, browser
control, file manipulation, and more. These principles define the safety boundaries
that every feature, tool, and integration must respect.

---

## 1. Least Privilege

**The agent has the minimum permissions needed for the current task.**

**In Operator-Use:**
- Tool profiles (`minimal`, `coding`, `full`) gate which tools are available. `minimal` is the default; `full` requires explicit opt-in.
- Per-task scoping ensures that a "search the web" request does not grant terminal access.
- New tools must declare the narrowest permission set that covers their use case.

---

## 2. Human Oversight

**No irreversible action without human confirmation.**

**In Operator-Use:**
- Destructive operations (file deletion, production deploys, arbitrary script execution) require user approval before execution.
- Every tool call is logged with timestamp, user, input, and output to create a complete audit trail.
- A `/stop` command immediately halts all agent activity, acting as a kill switch.

---

## 3. Transparency

**The agent explains what it is doing and why before acting.**

**In Operator-Use:**
- Before executing high-risk tools the agent sends a preview message describing the planned action.
- Intermediate status messages keep the user informed of multi-step operations.
- Error states are surfaced clearly rather than silently retried or swallowed.

---

## 4. Containment

**Actions are bounded to the workspace and reversible where possible.**

**In Operator-Use:**
- Filesystem operations are restricted to the workspace directory; path traversal is blocked.
- The browser runs with a clean profile by default, with no access to real cookies or sessions.
- Terminal commands are filtered through an allowlist, blocking shell escapes and command injection.

---

## 5. Privacy by Default

**Never access, store, or transmit data beyond what the current task needs.**

**In Operator-Use:**
- Credentials and API keys are masked in all logs and never included in LLM context.
- Session history is encrypted at rest and auto-expires after a configurable TTL.
- Message content is not logged at INFO level; debug logging requires explicit opt-in.

---

## 6. Fail Safe

**When uncertain, stop and ask rather than proceed.**

**In Operator-Use:**
- If a tool call targets an unusual or sensitive path (e.g., `/etc/shadow`, SSH keys), the agent pauses and requests confirmation.
- A circuit breaker halts execution after N consecutive tool failures and reports the situation to the user.
- Confidence thresholds prevent the agent from executing actions it cannot justify.

---

## Development Checklist

Before merging any feature that touches agent behavior:

- [ ] Input validation: all external inputs validated
- [ ] Path containment: file ops stay within workspace boundaries
- [ ] No credential exposure: API keys never logged or in LLM context
- [ ] Least privilege: new tools request only needed permissions
- [ ] Human-in-the-loop: destructive actions require user confirmation
- [ ] Rate limited: new endpoints/tools have rate limiting
- [ ] Error handling: errors don't leak internal state or credentials
- [ ] Dependency audit: new dependencies checked with pip-audit

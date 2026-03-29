# Security Guardrails & AI Principles Roadmap

> This document defines the security hardening plan and AI safety principles
> for Operator-Use. Each item is prioritized and actionable.

---

## Phase 1: Critical Fixes (Do Before Any Production Use)

### 1.1 Enforce Workspace Boundaries (Path Traversal Fix)

**File:** `operator_use/utils/helper.py` — `resolve()` function

**Problem:** Absolute paths bypass workspace boundaries. The LLM can read/write any file on the system.

**Fix:**
```python
def resolve(base: str | Path, path: str | Path) -> Path:
    base = Path(base).resolve()
    resolved = (base / Path(path)).resolve()
    # SECURITY: Ensure resolved path is within the workspace
    if not str(resolved).startswith(str(base)):
        raise PermissionError(
            f"Path traversal blocked: {path!r} resolves outside workspace {base}"
        )
    return resolved
```

**Affected tools:** read_file, write_file, edit_file, list_dir, patch_file

---

### 1.2 Sandbox Browser Authentication

**File:** `operator_use/web/browser/service.py` — `_copy_auth_files()`

**Problem:** Copies real Chrome cookies, login data, and sessions into the automated browser. The LLM has access to every logged-in account.

**Fix options (choose one):**
1. **Default to clean profile** — Don't copy auth files unless explicitly opted in via config flag `browser.copy_auth: true`
2. **Domain allowlist** — Only carry cookies for domains the user explicitly approves
3. **Separate browser profile** — Create an isolated profile; user logs in manually to only the sites needed

**Recommended:** Option 1 (default safe, opt-in dangerous)

---

### 1.3 Restrict JavaScript Execution

**File:** `operator_use/web/tools/browser.py` — `script` action

**Problem:** LLM can execute arbitrary JavaScript in the browser context, including exfiltrating cookies and tokens.

**Fix options:**
1. **Remove the `script` action entirely** if not essential
2. **JavaScript allowlist** — Only allow pre-approved script patterns (e.g., DOM queries, scroll, visibility checks)
3. **CSP-style sandbox** — Execute scripts in a sandboxed iframe or Web Worker
4. **Human-in-the-loop** — Require user confirmation before any script execution

**Recommended:** Option 4 for now, Option 2 long-term

---

### 1.4 Harden Terminal Command Controls

**File:** `operator_use/agent/tools/builtin/terminal.py`

**Problem:** Blocklist is trivially bypassable via nested shells, language interpreters, encoding tricks.

**Fix — Switch to allowlist approach:**
```python
ALLOWED_COMMAND_PREFIXES = {
    "git", "ls", "cat", "head", "tail", "grep", "find", "echo",
    "pip", "npm", "node", "python", "pytest", "cargo", "go",
    "curl", "wget",  # Consider removing if not needed
    "docker", "kubectl",
    # Add project-specific commands as needed
}

def _is_command_allowed(cmd: str) -> bool:
    normalized = cmd.strip().split()[0] if cmd.strip() else ""
    base_cmd = Path(normalized).name  # Handle /usr/bin/git -> git
    return base_cmd in ALLOWED_COMMAND_PREFIXES
```

**Additional hardening:**
- Block pipe to shell (`| bash`, `| sh`, `| zsh`)
- Block command substitution (`$(...)`, backticks)
- Block `eval`, `exec`, `source` as subcommands
- Add `--read-only` mode for terminal that only allows non-modifying commands

---

### 1.5 Sanitize File Downloads

**File:** `operator_use/web/tools/browser.py` — `download` action

**Fixes:**
```python
import os
from urllib.parse import urlparse

def _validate_download(url: str, filename: str, downloads_dir: Path) -> str | None:
    """Return error message if invalid, None if safe."""
    # Protocol check
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Blocked: only http/https downloads allowed, got {parsed.scheme}"

    # Filename sanitization
    safe_name = os.path.basename(filename)
    if safe_name != filename or ".." in filename:
        return f"Blocked: invalid filename {filename!r}"

    # Path containment
    target = (downloads_dir / safe_name).resolve()
    if not str(target).startswith(str(downloads_dir.resolve())):
        return f"Blocked: path traversal in filename"

    # Size limit (check Content-Length header before downloading)
    return None

MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024  # 100MB
```

---

## Phase 2: High Priority (Within Next Sprint)

### 2.1 Human-in-the-Loop for Dangerous Actions

Implement a confirmation system for high-risk operations:

```python
# In operator_use/agent/hooks/events.py
class ConfirmationRequired(Exception):
    """Raised when a tool needs user approval before executing."""
    def __init__(self, tool_name: str, description: str, details: dict):
        self.tool_name = tool_name
        self.description = description
        self.details = details

# Actions requiring confirmation:
CONFIRM_REQUIRED = {
    "terminal": lambda cmd: not _is_safe_readonly(cmd),
    "write_file": lambda path: _is_sensitive_path(path),
    "browser.script": lambda: True,  # Always confirm JS execution
    "browser.download": lambda: True,  # Always confirm downloads
}
```

This sends a message back to the user's channel asking "The agent wants to run `rm -rf build/`. Allow? [Yes/No]"

---

### 2.2 Rate Limiting

```python
# In operator_use/gateway/service.py
from collections import defaultdict
import time

class RateLimiter:
    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def check(self, user_id: str) -> bool:
        now = time.time()
        self._requests[user_id] = [
            t for t in self._requests[user_id] if now - t < self.window
        ]
        if len(self._requests[user_id]) >= self.max_requests:
            return False
        self._requests[user_id].append(now)
        return True
```

Apply to: gateway channels, tool execution, LLM API calls.

---

### 2.3 Credential Masking in Logs

```python
import re

SENSITIVE_PATTERNS = [
    re.compile(r'(sk-[a-zA-Z0-9]{20,})'),           # OpenAI-style
    re.compile(r'(gsk_[a-zA-Z0-9]{20,})'),           # Groq
    re.compile(r'(AIza[a-zA-Z0-9_-]{35})'),           # Google
    re.compile(r'(nvapi-[a-zA-Z0-9_-]{20,})'),        # NVIDIA
    re.compile(r'(sk-or-v1-[a-zA-Z0-9]{20,})'),       # OpenRouter
    re.compile(r'(Bearer\s+[a-zA-Z0-9._-]{20,})'),    # Bearer tokens
]

def mask_credentials(text: str) -> str:
    for pattern in SENSITIVE_PATTERNS:
        text = pattern.sub(lambda m: m.group()[:8] + '***REDACTED***', text)
    return text
```

Apply this as a logging filter across all modules.

---

### 2.4 Fix `allow_from` Semantics

**Problem:** Ambiguous whether empty list means "allow all" or "deny all."

**Fix:** Make it explicit and default-deny:

```python
# In gateway channel base
def is_allowed(self, user_id: str) -> bool:
    allow_list = self._cfg("allow_from") or []
    if not allow_list:
        logger.warning("allow_from is empty — denying all access (default-deny)")
        return False
    return str(user_id) in allow_list
```

---

## Phase 3: AI Safety Principles (Integrate into Development)

### 3.1 Core AI Principles for Operator-Use

These principles should govern all development decisions:

#### Principle 1: Least Privilege
> The agent should have the minimum permissions needed for the current task.

- **Implementation:** Tool profiles (`minimal`, `coding`, `full`) already exist. Make `minimal` the default. Require explicit opt-in for `full`.
- **Per-task scoping:** When a user asks "search the web for X," the agent should NOT have terminal access for that task.

#### Principle 2: Human Oversight
> No irreversible action without human confirmation.

- **Implementation:** See Phase 2.1 (confirmation system)
- **Audit trail:** Log every tool call with timestamp, user, input, output
- **Kill switch:** Implement a `/stop` command that immediately halts all agent activity

#### Principle 3: Transparency
> The agent must explain what it's doing and why.

- **Implementation:** The `intermediate_message` tool already exists. Make it mandatory before executing high-risk tools.
- **Action preview:** Before executing terminal commands or browser actions, send a preview message to the user.

#### Principle 4: Containment
> Agent actions should be bounded and reversible.

- **Implementation:**
  - Filesystem: workspace boundaries (Phase 1.1)
  - Browser: clean profile by default (Phase 1.2)
  - Terminal: allowlist (Phase 1.4)
  - Network: outbound request allowlist (new)
  - Time: max execution time per task

#### Principle 5: Privacy by Default
> Never access, store, or transmit user data beyond what's needed.

- **Implementation:**
  - Encrypt session history at rest
  - Auto-expire sessions after configurable TTL
  - Don't log message content at INFO level
  - Strip PII from LLM context where possible

#### Principle 6: Fail Safe
> When uncertain, the agent should stop and ask rather than proceed.

- **Implementation:**
  - Confidence thresholds for tool execution
  - If the LLM's tool call seems unusual (e.g., accessing `/etc/shadow`), pause and confirm
  - Circuit breaker: if N consecutive tool calls fail, halt and report

---

### 3.2 Security Controls Checklist for New Features

Before merging any new feature, verify:

- [ ] **Input validation:** All external inputs (user messages, API responses, file contents) are validated
- [ ] **Path containment:** File operations stay within workspace boundaries
- [ ] **No credential exposure:** API keys, tokens, and passwords are never logged, returned to users, or included in LLM context
- [ ] **Least privilege:** New tools request only the permissions they need
- [ ] **Human-in-the-loop:** Destructive or irreversible actions require confirmation
- [ ] **Rate limited:** New endpoints/tools have rate limiting
- [ ] **Error handling:** Errors don't leak internal state, paths, or credentials
- [ ] **Dependency audit:** New dependencies checked with `pip-audit`

---

### 3.3 Recommended Tooling

| Tool | Purpose | Integration Point |
|---|---|---|
| `pip-audit` | Dependency vulnerability scanning | CI pipeline |
| `bandit` | Python static security analysis | Pre-commit hook |
| `semgrep` | Custom security rule enforcement | CI pipeline |
| `gitleaks` | Detect secrets in git history | Pre-commit hook + CI |
| `safety` | Python dependency safety check | CI pipeline |

**Pre-commit config (`.pre-commit-config.yaml`):**
```yaml
repos:
  - repo: https://github.com/PyCQA/bandit
    rev: 1.8.3
    hooks:
      - id: bandit
        args: ["-r", "operator_use/", "-ll"]
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.24.3
    hooks:
      - id: gitleaks
```

---

## Phase 4: Long-Term Architecture (Roadmap Items)

### 4.1 Sandboxed Execution Environment
- Run agent tool execution inside a container (Docker/Firecracker)
- Filesystem access only to mounted workspace volume
- Network access controlled by container firewall rules
- Resource limits (CPU, memory, disk)

### 4.2 Capability-Based Access Control
```
User → Channel → Agent → Role → Capabilities
                                    ├── filesystem.read
                                    ├── filesystem.write (workspace only)
                                    ├── terminal.readonly
                                    ├── browser.navigate
                                    └── browser.script (requires approval)
```

### 4.3 Audit Log System
- Structured JSON logs for every tool invocation
- Immutable append-only log store
- Alerting on suspicious patterns (e.g., reading SSH keys, accessing /etc/passwd)
- Dashboard for reviewing agent activity

### 4.4 Prompt Injection Defense
- Input/output classifiers to detect prompt injection attempts
- Separate "system" and "user" context with clear boundaries
- Canary tokens in system prompts to detect extraction attempts
- Regular red-team testing with adversarial prompts

### 4.5 Session Security
- Encrypt session history at rest (AES-256)
- Configurable session TTL with auto-expiry
- Session isolation between users/channels
- Option to disable session persistence entirely

---

## Priority Matrix

| Priority | Item | Effort | Impact |
|---|---|---|---|
| P0 | 1.1 Path traversal fix | Small | Critical |
| P0 | 1.2 Browser auth sandbox | Medium | Critical |
| P0 | 1.3 JS execution restriction | Small | Critical |
| P0 | 1.4 Terminal allowlist | Small | High |
| P0 | 1.5 Download sanitization | Small | High |
| P1 | 2.1 Human-in-the-loop | Medium | High |
| P1 | 2.2 Rate limiting | Small | Medium |
| P1 | 2.3 Credential masking | Small | Medium |
| P1 | 2.4 allow_from fix | Small | Medium |
| P2 | 3.3 Security tooling (CI) | Medium | High |
| P3 | 4.1 Sandboxed execution | Large | Critical |
| P3 | 4.2 Capability-based ACL | Large | High |
| P3 | 4.3 Audit log system | Medium | High |
| P3 | 4.4 Prompt injection defense | Large | High |
| P3 | 4.5 Session encryption | Medium | Medium |

---

## Immediate Action Items

1. **Revoke and rotate** all API keys currently in `.env` on the development machine
2. Apply the `resolve()` fix (Phase 1.1) — this is a 5-line change with critical impact
3. Set `browser.copy_auth: false` as default
4. Add `bandit` to pre-commit hooks
5. Run `pip-audit` on current dependencies
6. Schedule a security review with the full team

---

*Generated: 2026-03-29 | For: Operator-Use startup team*
*Review and update quarterly or after major feature additions.*

# Sandbox

The sandbox enforces a `SandboxPolicy` before every tool call. Python-level checks apply to all tools; OS-level sandboxing wraps the `terminal` tool's subprocess.

## Architecture

```
operator_use/sandbox/
  policy.py     ← SandboxPolicy dataclass and presets
  service.py    ← Sandbox hook service, OS-level command builders
```

`Sandbox` registers a `before_tool_call` hook via `Hooks`. The hook fires before every tool execution, runs the Python-level policy checks, and for the `terminal` tool optionally wraps the subprocess in an OS-level sandbox.

## Policy modes

| Mode | Python checks | OS sandbox | Behaviour |
|---|---|---|---|
| `off` | No | No | No restrictions (default) |
| `warn` | Yes | No | Log violations to stderr, never block |
| `enforce` | Yes | Optional | Block policy violations before execution |

`SandboxPolicy.strict(cwd)` is a preset that sets mode `enforce`, locks writes to `cwd`, and enables OS sandboxing when available.

## Setting the mode

Via CLI:

```bash
operator --sandbox strict
operator --sandbox enforce
operator --sandbox warn
operator --sandbox off
```

Via `RuntimeConfig`:

```python
from operator_use.runtime.types import RuntimeConfig

config = RuntimeConfig(cwd="/my/project", sandbox="strict")
```

## Python-level policy

`SandboxPolicy` fields:

| Field | Type | Default | Description |
|---|---|---|---|
| `mode` | `'off' \| 'warn' \| 'enforce'` | `'enforce'` | Policy mode |
| `allowed_write_paths` | `list[str] \| None` | `None` | Dirs the agent may write to; `None` = unrestricted |
| `allowed_read_paths` | `list[str] \| None` | `None` | Dirs the agent may read from; `None` = unrestricted |
| `allow_shell` | `bool` | `True` | Whether shell commands are permitted |
| `blocked_command_patterns` | `list[str]` | `[]` | Regex patterns that block shell commands |
| `allow_network` | `bool` | `True` | Whether network tool calls are permitted |
| `os_sandbox` | `'auto' \| 'sandbox-exec' \| 'bwrap' \| None` | `'auto'` | OS-level sandbox tool |

### `strict` preset

```python
SandboxPolicy.strict(cwd="/my/project")
# → mode=enforce, allowed_write_paths=[cwd], os_sandbox='auto'
```

### `permissive` preset

```python
SandboxPolicy.permissive()
# → mode=warn, allowed_write_paths=None, os_sandbox=None
```

### `off` preset

```python
SandboxPolicy.off()
# → mode=off (all checks skipped)
```

## OS-level sandboxing

The terminal tool's subprocess gets wrapped in an OS-level sandbox in `enforce` mode when an OS sandbox is available.

### macOS — Apple Seatbelt (`sandbox-exec`)

```
sandbox-exec -p "(version 1) (allow default) (deny file-write* (regex \".*\")) (allow file-write* <write-subtrees>)"
```

- No root required
- Read-only by default; permitted write paths are added as `(subpath ...)` rules
- `/dev/null`, `/dev`, `/tmp`, and `/private/tmp` are always writable

### Linux — bubblewrap (`bwrap`)

```
bwrap --ro-bind / / --bind <write-path> <write-path> --dev /dev --proc /proc --tmpfs /tmp <cmd>
```

- No root required (user namespace)
- Whole filesystem is read-only; permitted write subtrees are bind-mounted writable
- `/tmp` is a fresh `tmpfs` (does not leak host `/tmp`)

### Windows

Python-level checks only. No kernel sandbox is applied without administrator privileges.

## Detection

The sandbox auto-detects the best available tool:

```python
from operator_use.sandbox.policy import _detect_os_sandbox

tool = _detect_os_sandbox()
# → 'sandbox-exec' on macOS (if available)
# → 'bwrap' on Linux (if available)
# → None on Windows or if neither tool is found
```

## Violation semantics

- In `warn` mode: logs the violation at WARNING level and continues execution.
- In `enforce` mode: raises a `ToolResult.error(...)` before execution; the LLM sees the error and can decide how to proceed.

## Related documents

- [docs/tool.md](./tool.md) — Tool interface and hooks
- [docs/hooks.md](./hooks.md) — `before_tool_call` hook event
- [docs/agent.md](./agent.md) — Where Sandbox is wired into the agent

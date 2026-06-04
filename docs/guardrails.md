# Guardrails

Guardrails are pluggable per-turn controllers that intercept every tool call
before and after execution. They implement policy — loop detection, safety rules,
rate limits, resource guards — without touching the Engine or Agent core.

## Interface

```python
class Guardrail(ABC):
    name: str
    description: str

    def on_turn_start(self) -> None: ...        # reset per-turn state
    def on_turn_end(self) -> None: ...          # cleanup / telemetry

    async def before_call(
        self, invocation: ToolInvocation, context: ToolContext
    ) -> GuardrailDecision: ...

    async def after_call(
        self, invocation: ToolInvocation, result: ToolResult, context: ToolContext
    ) -> GuardrailDecision: ...
```

`before_call` is called before the tool executes. `after_call` is called after the
tool returns its result (successful or error). Both return a `GuardrailDecision`.

## GuardrailDecision

```python
@dataclass
class GuardrailDecision:
    action: str = "allow"   # allow | warn | block | halt
    reason: str = ""
    code: str = ""
```

| Action | `before_call` effect | `after_call` effect |
|---|---|---|
| `allow` | Tool executes normally | Result passes unchanged |
| `warn` | Tool executes; warning appended to result | Warning text appended to result content |
| `block` | Tool is skipped; synthetic error result returned to LLM | Turn aborts; abort signal sent to Engine |
| `halt` | Turn aborts; abort signal sent to Engine | Turn aborts; abort signal sent to Engine |

`block` returns the `reason` as a `ToolResultContent(is_error=True)` so the LLM
sees it as a tool error and can respond. `halt` signals the Engine to abort the
entire turn immediately — the LLM does not get a follow-up call.

## Loading

Guardrails are loaded from Python files. Each file must export:
- `guardrail = MyGuardrail()` — a single instance, or
- `guardrails = [...]` — a list of instances

Files starting with `_` are skipped.

Discovery order (first-found wins on name collision):

| Location | Purpose |
|---|---|
| `operator_use/builtins/guardrails/` | Shipped built-in guardrails |
| `~/.operator/profiles/<name>/guardrails/` | Active profile's custom guardrails |
| `<project>/.operator/guardrails/` | Project-level guardrails (loaded when Operator runs in that repo) |

On top of file-loaded guardrails, extensions may register guardrails at runtime via
`api.register_guardrail()`. File-loaded names win — if a file and an extension both
define a guardrail named `"my_guard"`, the file version is used.

`ResourceLoader._reload_guardrails()` is called during `reload()`. After reload,
`Agent._refresh_guardrails()` merges the updated file-loaded set with any
extension-registered guardrails and replaces `self._guardrails`.

## Lifecycle in a turn

```
invoke()
  │
  └─ guardrail.on_turn_start()  ← reset per-turn state for all guardrails
      │
      └─ for each tool call:
           before_call()  ← block/halt short-circuits execution
           tool.execute()
           after_call()   ← warn appends to result, halt aborts
      │
      └─ guardrail.on_turn_end()  ← after Engine.run() completes
```

## Built-in: LoopDetectionGuardrail

`operator_use/builtins/guardrails/loop_detection.py` ships as the default guardrail.

Tracks three loop patterns within a single turn:

| Pattern | Warn threshold | Block/Halt threshold |
|---|---|---|
| Exact failure — same tool + same args failed | ≥ 2 → warn | ≥ 5 → block |
| Same-tool failure — same tool (any args) failed | ≥ 3 → warn | ≥ 8 → halt |
| Idempotent no-progress — read-only tool returned same result | ≥ 2 → warn | ≥ 5 → block |

All counters are reset at `on_turn_start()`.

Idempotent tools tracked: `read`, `glob`, `grep`, `ls`, `web_search`, `web_fetch`,
`knowledge`, `memory`.

Mutating tools tracked: `terminal`, `edit`, `write`, `browser`, `computer`,
`send`, `cron`, `workflow`, `subagent`, `team`, `peer_agent`.

## Writing a custom guardrail

```python
# ~/.operator/profiles/<name>/guardrails/my_guard.py
from operator_use.guardrail.types import Guardrail, GuardrailDecision

class SensitiveFileGuardrail(Guardrail):
    def __init__(self):
        super().__init__("sensitive_file_guard", "Block writes to protected paths.")

    def on_turn_start(self) -> None:
        pass  # nothing to reset

    async def before_call(self, invocation, context) -> GuardrailDecision:
        if invocation.name in ("write", "edit"):
            path = invocation.params.get("file_path", "")
            if "/etc/" in path or "/.ssh/" in path:
                return GuardrailDecision(
                    action="block",
                    code="protected_path",
                    reason=f"Writes to {path} are not allowed.",
                )
        return GuardrailDecision()

    async def after_call(self, invocation, result, context) -> GuardrailDecision:
        return GuardrailDecision()

guardrail = SensitiveFileGuardrail()
```

## Registering from an extension

```python
from operator_use.guardrail.types import Guardrail, GuardrailDecision

class MyGuardrail(Guardrail):
    def __init__(self):
        super().__init__("my_guard", "Example extension guardrail.")

    def on_turn_start(self): pass

    async def before_call(self, invocation, context) -> GuardrailDecision:
        return GuardrailDecision()

    async def after_call(self, invocation, result, context) -> GuardrailDecision:
        return GuardrailDecision()

def extension(api):
    api.register_guardrail(MyGuardrail())
```

File-loaded guardrails take precedence over extension-registered ones with the same
name. Extension guardrails are re-merged on every `reload()`.

## Related documents

- [agent.md](./agent.md) — `_refresh_guardrails`, guardrail lifecycle wiring
- [extensions.md](./extensions.md) — `api.register_guardrail()`
- [tool.md](./tool.md) — `ToolInvocation`, `ToolResult`, `ToolContext`

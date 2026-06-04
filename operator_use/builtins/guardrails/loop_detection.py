from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from operator_use.guardrail.types import Guardrail, GuardrailDecision

if TYPE_CHECKING:
    from operator_use.tool.types import ToolInvocation, ToolResult, ToolContext


# Tools that always return the same result for the same args — repeating them unchanged
# without acting on the result is a loop signal.
_IDEMPOTENT = frozenset({
    "read", "glob", "grep", "ls",
    "web_search", "web_fetch",
    "knowledge", "memory",
})

# Tools that mutate state — repeating them after failure is a loop signal.
_MUTATING = frozenset({
    "terminal", "edit", "write",
    "browser", "computer",
    "send", "cron", "workflow",
    "subagent", "team", "peer_agent",
})

_EXACT_FAILURE_WARN = 2
_EXACT_FAILURE_BLOCK = 5
_TOOL_FAILURE_WARN = 3
_TOOL_FAILURE_HALT = 8
_NO_PROGRESS_WARN = 2
_NO_PROGRESS_BLOCK = 5


def _sig(tool_name: str, params: dict) -> str:
    canonical = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return f"{tool_name}:{hashlib.sha256(canonical.encode()).hexdigest()}"


def _result_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


class LoopDetectionGuardrail(Guardrail):
    """Detects repeated identical/failing tool calls within a single turn.

    Tracks three loop patterns (ported from Hermes tool_guardrails.py):
      - Exact failure: same tool + same args failed N times → block
      - Same-tool failure: same tool (any args) failed N times → halt
      - Idempotent no-progress: read-only tool returned the same result N times → block
    """

    def __init__(self) -> None:
        super().__init__("loop_detection", "Detects repeated identical or failing tool calls within a turn")
        self._exact_failures: dict[str, int] = {}
        self._tool_failures: dict[str, int] = {}
        self._no_progress: dict[str, tuple[str, int]] = {}  # sig → (result_hash, count)

    def on_turn_start(self) -> None:
        self._exact_failures.clear()
        self._tool_failures.clear()
        self._no_progress.clear()

    async def before_call(self, invocation: ToolInvocation, context: ToolContext) -> GuardrailDecision:
        sig = _sig(invocation.name, invocation.params)
        exact_count = self._exact_failures.get(sig, 0)
        if exact_count >= _EXACT_FAILURE_BLOCK:
            return GuardrailDecision(
                action="block",
                code="repeated_exact_failure_block",
                reason=(
                    f"{invocation.name} failed {exact_count} times with identical arguments. "
                    "Stop retrying it unchanged — change strategy or explain the blocker."
                ),
            )

        if invocation.name in _IDEMPOTENT and invocation.name not in _MUTATING:
            record = self._no_progress.get(sig)
            if record is not None:
                _, repeat_count = record
                if repeat_count >= _NO_PROGRESS_BLOCK:
                    return GuardrailDecision(
                        action="block",
                        code="idempotent_no_progress_block",
                        reason=(
                            f"{invocation.name} returned the same result {repeat_count} times. "
                            "Use the result already provided or try a different query."
                        ),
                    )

        return GuardrailDecision()

    async def after_call(self, invocation: ToolInvocation, result: ToolResult, context: ToolContext) -> GuardrailDecision:
        sig = _sig(invocation.name, invocation.params)

        if result.is_error:
            exact_count = self._exact_failures.get(sig, 0) + 1
            self._exact_failures[sig] = exact_count
            self._no_progress.pop(sig, None)

            tool_count = self._tool_failures.get(invocation.name, 0) + 1
            self._tool_failures[invocation.name] = tool_count

            if tool_count >= _TOOL_FAILURE_HALT:
                return GuardrailDecision(
                    action="halt",
                    code="same_tool_failure_halt",
                    reason=(
                        f"{invocation.name} failed {tool_count} times this turn. "
                        "Stop retrying the same failing tool path and choose a different approach."
                    ),
                )

            if exact_count >= _EXACT_FAILURE_WARN:
                return GuardrailDecision(
                    action="warn",
                    code="repeated_exact_failure_warning",
                    reason=(
                        f"{invocation.name} has failed {exact_count} times with identical arguments. "
                        "Inspect the error and change strategy instead of retrying unchanged."
                    ),
                )

            if tool_count >= _TOOL_FAILURE_WARN:
                return GuardrailDecision(
                    action="warn",
                    code="same_tool_failure_warning",
                    reason=_recovery_hint(invocation.name, tool_count),
                )

            return GuardrailDecision()

        # Successful call — clear failure counters for this signature
        self._exact_failures.pop(sig, None)
        self._tool_failures.pop(invocation.name, None)

        # Idempotent no-progress tracking
        if invocation.name in _IDEMPOTENT and invocation.name not in _MUTATING:
            rh = _result_hash(result.content)
            previous = self._no_progress.get(sig)
            count = 1
            if previous is not None and previous[0] == rh:
                count = previous[1] + 1
            self._no_progress[sig] = (rh, count)

            if count >= _NO_PROGRESS_WARN:
                return GuardrailDecision(
                    action="warn",
                    code="idempotent_no_progress_warning",
                    reason=(
                        f"{invocation.name} returned the same result {count} times. "
                        "Use the result already provided or change the query."
                    ),
                )

        return GuardrailDecision()


def _recovery_hint(tool_name: str, count: int) -> str:
    common = (
        f"{tool_name} has failed {count} times this turn. "
        "Diagnose before retrying — inspect the latest error and verify your assumptions. "
    )
    if tool_name == "terminal":
        return common + (
            "Try a diagnostic like `pwd && ls -la`, then use an absolute path, "
            "a simpler command, or a different working directory."
        )
    return common + (
        "Try different arguments, a narrower path, or a different tool that can make progress."
    )


guardrail = LoopDetectionGuardrail()

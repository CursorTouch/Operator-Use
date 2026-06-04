from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from operator_use.tool.types import ToolInvocation, ToolResult, ToolContext


@dataclass
class GuardrailDecision:
    """Decision returned by a guardrail check."""

    action: str = "allow"  # allow | warn | block | halt
    reason: str = ""
    code: str = ""

    @property
    def allows_execution(self) -> bool:
        return self.action in {"allow", "warn"}

    @property
    def should_halt(self) -> bool:
        return self.action in {"block", "halt"}


@dataclass
class GuardrailError:
    """File-level guardrail load failure with optional stack trace."""

    path: str
    error: str
    stack: str = ""


@dataclass
class LoadGuardrailsResult:
    """Aggregate result of loading guardrails from one or more directories."""

    guardrails: list[Guardrail] = field(default_factory=list)
    errors: list[GuardrailError] = field(default_factory=list)


class Guardrail(ABC):
    """Abstract base for guardrails: stateful per-turn controllers that intercept tool calls.

    Subclasses must implement before_call and after_call. The lifecycle hooks
    on_turn_start / on_turn_end are optional — override them to manage per-turn state
    (e.g. resetting failure counters at the start of each turn).

    Loading: export `guardrail = MyGuardrail()` or `guardrails = [...]` from a .py file
    placed in builtins/guardrails/, a profile's guardrails/ dir, or registered via
    api.register_guardrail() in an extension.
    """

    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description

    def on_turn_start(self) -> None:
        """Called once at the start of each turn. Reset per-turn state here."""

    def on_turn_end(self) -> None:
        """Called once after each turn completes. Use for cleanup or telemetry."""

    @abstractmethod
    async def before_call(
        self,
        invocation: ToolInvocation,
        context: ToolContext,
    ) -> GuardrailDecision:
        """Inspect the pending tool call before execution.

        Return allow/warn to permit execution, block to return a synthetic error
        result without executing the tool, or halt to abort the entire turn.
        """
        ...

    @abstractmethod
    async def after_call(
        self,
        invocation: ToolInvocation,
        result: ToolResult,
        context: ToolContext,
    ) -> GuardrailDecision:
        """Inspect the completed tool result after execution.

        Return allow to pass the result unchanged, warn to append a warning
        message to the result, or halt to abort the rest of the turn.
        """
        ...

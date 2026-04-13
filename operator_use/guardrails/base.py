"""Base classes for the guardrails module.

Defines the abstract foundation for action validation, content filtering,
and policy enforcement used throughout the guardrails framework.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class GuardrailAction(str, Enum):
    """Possible outcomes from a guardrail check."""

    ALLOW = "allow"
    BLOCK = "block"
    CONFIRM = "confirm"


class RiskLevel(str, Enum):
    """Risk classification for an action."""

    SAFE = "safe"
    REVIEW = "review"
    DANGEROUS = "dangerous"


@dataclass
class GuardrailResult:
    """Result object returned by every guardrail check.

    Attributes:
        action:   Disposition — allow, block, or require human confirmation.
        reason:   Human-readable explanation for the decision.
        severity: Risk level associated with the decision.
        metadata: Optional extra data produced by the guardrail.
    """

    action: GuardrailAction
    reason: str
    severity: RiskLevel = RiskLevel.SAFE
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def allow(cls, reason: str = "OK", severity: RiskLevel = RiskLevel.SAFE) -> "GuardrailResult":
        return cls(action=GuardrailAction.ALLOW, reason=reason, severity=severity)

    @classmethod
    def block(cls, reason: str, severity: RiskLevel = RiskLevel.DANGEROUS) -> "GuardrailResult":
        return cls(action=GuardrailAction.BLOCK, reason=reason, severity=severity)

    @classmethod
    def confirm(cls, reason: str, severity: RiskLevel = RiskLevel.REVIEW) -> "GuardrailResult":
        return cls(action=GuardrailAction.CONFIRM, reason=reason, severity=severity)

    @property
    def is_allowed(self) -> bool:
        return self.action == GuardrailAction.ALLOW

    @property
    def is_blocked(self) -> bool:
        return self.action == GuardrailAction.BLOCK

    @property
    def needs_confirmation(self) -> bool:
        return self.action == GuardrailAction.CONFIRM


class Guardrail(ABC):
    """Abstract base for all guardrails.

    Every guardrail receives a context dict and returns a GuardrailResult.
    Subclasses implement :meth:`check` with their specific logic.
    """

    def __init__(self, name: str, enabled: bool = True) -> None:
        self.name = name
        self.enabled = enabled

    @abstractmethod
    def check(self, context: dict[str, Any]) -> GuardrailResult:
        """Evaluate the guardrail against the given context.

        Args:
            context: Arbitrary context dictionary provided by the caller.
                     Exact keys depend on the guardrail type.

        Returns:
            A :class:`GuardrailResult` describing the decision.
        """

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, enabled={self.enabled})"


class ActionValidator(Guardrail, ABC):
    """Validates tool calls *before* execution.

    Receives the tool name, arguments, and caller context, and decides
    whether to allow, block, or require confirmation.
    """

    @abstractmethod
    def validate(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: dict[str, Any],
    ) -> GuardrailResult:
        """Validate a pending tool call.

        Args:
            tool_name: Name of the tool about to be called.
            args:      Arguments that will be passed to the tool.
            context:   Caller context (agent id, session, etc.).

        Returns:
            A :class:`GuardrailResult` describing the decision.
        """

    def check(self, context: dict[str, Any]) -> GuardrailResult:
        """Delegate to :meth:`validate` using ``context`` keys."""
        return self.validate(
            tool_name=context.get("tool_name", ""),
            args=context.get("args", {}),
            context=context,
        )


class ContentFilter(Guardrail, ABC):
    """Filters LLM output *before* it is forwarded to the user.

    Receives the raw content string and caller context, and returns a
    result indicating whether the content should pass through, be
    blocked, or be sent for human review.
    """

    @abstractmethod
    def filter(self, content: str, context: dict[str, Any]) -> GuardrailResult:
        """Evaluate and potentially filter a content string.

        Args:
            content: The LLM-generated text to evaluate.
            context: Caller context (agent id, session, etc.).

        Returns:
            A :class:`GuardrailResult` describing the decision.
        """

    def check(self, context: dict[str, Any]) -> GuardrailResult:
        """Delegate to :meth:`filter` using ``context`` keys."""
        return self.filter(
            content=context.get("content", ""),
            context=context,
        )


class PolicyEngine(Guardrail, ABC):
    """Evaluates the risk level of an action given context.

    Concrete implementations encode organisation-specific policies.
    """

    @abstractmethod
    def classify_risk(self, action: dict[str, Any]) -> RiskLevel:
        """Classify the risk of ``action``.

        Args:
            action: A description of the action (tool name, args, agent, …).

        Returns:
            ``safe``, ``review``, or ``dangerous``.
        """

    def check(self, context: dict[str, Any]) -> GuardrailResult:
        """Run :meth:`classify_risk` and wrap the result."""
        risk = self.classify_risk(context)
        if risk == RiskLevel.SAFE:
            return GuardrailResult.allow("Policy: safe", severity=risk)
        if risk == RiskLevel.REVIEW:
            return GuardrailResult.confirm("Policy: requires review", severity=risk)
        return GuardrailResult.block("Policy: dangerous action", severity=risk)

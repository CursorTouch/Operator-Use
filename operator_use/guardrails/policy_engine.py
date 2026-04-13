"""PolicyEngine: risk classification and policy decisions.

Concrete engines extend :class:`~operator_use.guardrails.base.PolicyEngine`
and encode organisation-specific risk policies.

This module ships a :class:`RuleBasedPolicyEngine` that classifies actions
against configurable safe/dangerous tool lists, and a
:class:`CompositePolicyEngine` that aggregates multiple engines with
DANGEROUS > REVIEW > SAFE precedence.
"""

from __future__ import annotations

import logging
from typing import Any

from operator_use.guardrails.base import (
    GuardrailResult,
    PolicyEngine,
    RiskLevel,
)

logger = logging.getLogger(__name__)


class RuleBasedPolicyEngine(PolicyEngine):
    """Classifies risk by comparing ``action["tool_name"]`` against lists.

    Classification priority:
    1. If the tool name is in *dangerous_tools* → ``dangerous``
    2. If the tool name is in *review_tools* → ``review``
    3. Otherwise → ``safe``

    Example::

        engine = RuleBasedPolicyEngine(
            dangerous_tools={"shell", "delete_file"},
            review_tools={"write_file"},
        )
        assert engine.classify_risk({"tool_name": "shell"}) == RiskLevel.DANGEROUS
        assert engine.classify_risk({"tool_name": "write_file"}) == RiskLevel.REVIEW
        assert engine.classify_risk({"tool_name": "read_file"}) == RiskLevel.SAFE
    """

    def __init__(
        self,
        dangerous_tools: set[str] | None = None,
        review_tools: set[str] | None = None,
    ) -> None:
        super().__init__(name="rule_based_policy")
        self.dangerous_tools: set[str] = dangerous_tools or set()
        self.review_tools: set[str] = review_tools or set()

    def classify_risk(self, action: dict[str, Any]) -> RiskLevel:
        tool_name = action.get("tool_name", "")
        if tool_name in self.dangerous_tools:
            return RiskLevel.DANGEROUS
        if tool_name in self.review_tools:
            return RiskLevel.REVIEW
        return RiskLevel.SAFE


class CompositePolicyEngine(PolicyEngine):
    """Runs multiple engines and returns the highest risk level found.

    Priority: DANGEROUS > REVIEW > SAFE.
    """

    def __init__(self, engines: list[PolicyEngine] | None = None) -> None:
        super().__init__(name="composite_policy")
        self.engines: list[PolicyEngine] = engines or []

    def add(self, engine: PolicyEngine) -> None:
        """Append an engine to the chain."""
        self.engines.append(engine)

    def classify_risk(self, action: dict[str, Any]) -> RiskLevel:
        highest = RiskLevel.SAFE

        for engine in self.engines:
            if not engine.enabled:
                continue
            risk = engine.classify_risk(action)
            logger.debug("PolicyEngine %r classified as %s", engine.name, risk)
            if risk == RiskLevel.DANGEROUS:
                return RiskLevel.DANGEROUS
            if risk == RiskLevel.REVIEW:
                highest = RiskLevel.REVIEW

        return highest

    def check(self, context: dict[str, Any]) -> GuardrailResult:
        risk = self.classify_risk(context)
        if risk == RiskLevel.SAFE:
            return GuardrailResult.allow("CompositePolicyEngine: safe", severity=risk)
        if risk == RiskLevel.REVIEW:
            return GuardrailResult.confirm("CompositePolicyEngine: requires review", severity=risk)
        return GuardrailResult.block("CompositePolicyEngine: dangerous action", severity=risk)

"""operator_use.guardrails — base classes and registry for the guardrails framework.

Public API
----------

Base classes & result types:
    Guardrail, GuardrailResult, GuardrailAction, RiskLevel,
    ActionValidator, ContentFilter, PolicyEngine

Concrete helpers:
    AllowAllValidator, BlockListValidator, CompositeActionValidator,
    PassthroughFilter, KeywordBlockFilter, RegexFilter, CompositeContentFilter,
    RuleBasedPolicyEngine, CompositePolicyEngine

Registry:
    GuardrailRegistry
"""

from operator_use.guardrails.base import (
    Guardrail,
    GuardrailAction,
    GuardrailResult,
    RiskLevel,
    ActionValidator,
    ContentFilter,
    PolicyEngine,
)
from operator_use.guardrails.action_validator import (
    AllowAllValidator,
    BlockListValidator,
    CompositeActionValidator,
)
from operator_use.guardrails.content_filter import (
    PassthroughFilter,
    KeywordBlockFilter,
    RegexFilter,
    CompositeContentFilter,
)
from operator_use.guardrails.policy_engine import (
    RuleBasedPolicyEngine,
    CompositePolicyEngine,
)
from operator_use.guardrails.registry import GuardrailRegistry

__all__ = [
    # base
    "Guardrail",
    "GuardrailAction",
    "GuardrailResult",
    "RiskLevel",
    "ActionValidator",
    "ContentFilter",
    "PolicyEngine",
    # action validators
    "AllowAllValidator",
    "BlockListValidator",
    "CompositeActionValidator",
    # content filters
    "PassthroughFilter",
    "KeywordBlockFilter",
    "RegexFilter",
    "CompositeContentFilter",
    # policy engines
    "RuleBasedPolicyEngine",
    "CompositePolicyEngine",
    # registry
    "GuardrailRegistry",
]

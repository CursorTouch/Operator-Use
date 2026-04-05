from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum


class RiskLevel(Enum):
    SAFE = "safe"
    REVIEW = "review"
    DANGEROUS = "dangerous"

    def __gt__(self, other: RiskLevel) -> bool:
        order = [RiskLevel.SAFE, RiskLevel.REVIEW, RiskLevel.DANGEROUS]
        return order.index(self) > order.index(other)


class ActionPolicy(ABC):
    """Base class for policies that assess the risk level of agent tool calls."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def assess(self, tool_name: str, args: dict) -> RiskLevel: ...


class ContentFilter(ABC):
    """Base class for filters that sanitize content before logging or LLM ingestion."""

    @abstractmethod
    def filter(self, content: str) -> str: ...

    @abstractmethod
    def is_safe(self, content: str) -> bool: ...


class PolicyEngine:
    """Runs all registered ActionPolicies and returns the highest risk level."""

    def __init__(self, policies: list[ActionPolicy] | None = None) -> None:
        self._policies: list[ActionPolicy] = policies or []

    def add_policy(self, policy: ActionPolicy) -> None:
        self._policies.append(policy)

    def assess(self, tool_name: str, args: dict) -> RiskLevel:
        level = RiskLevel.SAFE
        for policy in self._policies:
            result = policy.assess(tool_name, args)
            if result > level:
                level = result
        return level

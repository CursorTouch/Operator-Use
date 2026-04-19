"""ContentFilter: post-execution output filtering.

Concrete filters extend :class:`~operator_use.guardrails.base.ContentFilter`
and plug into the response pipeline before content is forwarded to the user.

This module ships a :class:`CompositeContentFilter` that aggregates multiple
filters with the same BLOCK > CONFIRM > ALLOW precedence used elsewhere.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from operator_use.guardrails.base import (
    ContentFilter,
    GuardrailAction,
    GuardrailResult,
    RiskLevel,
)

logger = logging.getLogger(__name__)


class PassthroughFilter(ContentFilter):
    """No-op filter — passes all content through.

    Useful as a default or in test environments.
    """

    def __init__(self) -> None:
        super().__init__(name="passthrough")

    def filter(self, content: str, context: dict[str, Any]) -> GuardrailResult:
        return GuardrailResult.allow("PassthroughFilter: content accepted")


class KeywordBlockFilter(ContentFilter):
    """Blocks content that contains any phrase from a configurable deny-list.

    Matching is case-insensitive by default.

    Example::

        f = KeywordBlockFilter(blocked_phrases={"drop table", "rm -rf"})
        result = f.filter("please run rm -rf /", {})
        assert result.is_blocked
    """

    def __init__(
        self,
        blocked_phrases: set[str] | None = None,
        case_sensitive: bool = False,
    ) -> None:
        super().__init__(name="keyword_block")
        self.blocked_phrases: set[str] = blocked_phrases or set()
        self.case_sensitive = case_sensitive

    def filter(self, content: str, context: dict[str, Any]) -> GuardrailResult:
        haystack = content if self.case_sensitive else content.lower()
        for phrase in self.blocked_phrases:
            needle = phrase if self.case_sensitive else phrase.lower()
            if needle in haystack:
                return GuardrailResult.block(
                    f"Content contains blocked phrase: {phrase!r}",
                    severity=RiskLevel.DANGEROUS,
                )
        return GuardrailResult.allow("No blocked phrases found")


class RegexFilter(ContentFilter):
    """Blocks content matching any of a set of regular expressions.

    Example::

        f = RegexFilter(patterns=[r"\\bpassword\\s*=\\s*\\S+"])
        result = f.filter("password = hunter2", {})
        assert result.is_blocked
    """

    def __init__(self, patterns: list[str] | None = None) -> None:
        super().__init__(name="regex_filter")
        self._compiled = [re.compile(p) for p in (patterns or [])]

    def filter(self, content: str, context: dict[str, Any]) -> GuardrailResult:
        for pattern in self._compiled:
            if pattern.search(content):
                return GuardrailResult.block(
                    f"Content matched blocked pattern: {pattern.pattern!r}",
                    severity=RiskLevel.DANGEROUS,
                )
        return GuardrailResult.allow("No patterns matched")


class CompositeContentFilter(ContentFilter):
    """Runs multiple filters in order and returns the strictest result.

    Priority: BLOCK > CONFIRM > ALLOW.  The first BLOCK short-circuits.
    """

    def __init__(self, filters: list[ContentFilter] | None = None) -> None:
        super().__init__(name="composite_content_filter")
        self.filters: list[ContentFilter] = filters or []

    def add(self, content_filter: ContentFilter) -> None:
        """Append a filter to the chain."""
        self.filters.append(content_filter)

    def filter(self, content: str, context: dict[str, Any]) -> GuardrailResult:
        result: GuardrailResult = GuardrailResult.allow("No filters configured")

        for f in self.filters:
            if not f.enabled:
                continue
            current = f.filter(content, context)
            logger.debug(
                "ContentFilter %r: %s — %s",
                f.name,
                current.action,
                current.reason,
            )
            if current.action == GuardrailAction.BLOCK:
                return current
            if current.action == GuardrailAction.CONFIRM:
                result = current

        return result

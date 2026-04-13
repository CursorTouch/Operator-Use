"""ActionValidator: pre-execution tool-call validation.

Concrete validators extend :class:`~operator_use.guardrails.base.ActionValidator`
and register themselves with :class:`~operator_use.guardrails.registry.GuardrailRegistry`.

This module also ships a :class:`CompositeActionValidator` that runs a sequence
of validators and returns the most restrictive result.
"""

from __future__ import annotations

import logging
from typing import Any

from operator_use.guardrails.base import (
    ActionValidator,
    GuardrailAction,
    GuardrailResult,
    RiskLevel,
)

logger = logging.getLogger(__name__)


class AllowAllValidator(ActionValidator):
    """Passthrough validator — allows every tool call.

    Useful as a no-op default or for testing.
    """

    def __init__(self) -> None:
        super().__init__(name="allow_all")

    def validate(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: dict[str, Any],
    ) -> GuardrailResult:
        return GuardrailResult.allow(f"AllowAllValidator: {tool_name!r} permitted")


class BlockListValidator(ActionValidator):
    """Blocks any tool whose name appears in a configurable deny-list.

    Example::

        validator = BlockListValidator(blocked_tools={"shell", "delete_file"})
        result = validator.validate("shell", {}, {})
        assert result.is_blocked
    """

    def __init__(self, blocked_tools: set[str] | None = None) -> None:
        super().__init__(name="block_list")
        self.blocked_tools: set[str] = blocked_tools or set()

    def validate(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: dict[str, Any],
    ) -> GuardrailResult:
        if tool_name in self.blocked_tools:
            return GuardrailResult.block(
                f"Tool {tool_name!r} is on the deny-list",
                severity=RiskLevel.DANGEROUS,
            )
        return GuardrailResult.allow(f"Tool {tool_name!r} is not blocked")


class CompositeActionValidator(ActionValidator):
    """Runs multiple validators in order and returns the strictest result.

    Priority: BLOCK > CONFIRM > ALLOW.  The first BLOCK short-circuits.
    """

    def __init__(self, validators: list[ActionValidator] | None = None) -> None:
        super().__init__(name="composite_action_validator")
        self.validators: list[ActionValidator] = validators or []

    def add(self, validator: ActionValidator) -> None:
        """Append a validator to the chain."""
        self.validators.append(validator)

    def validate(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: dict[str, Any],
    ) -> GuardrailResult:
        result: GuardrailResult = GuardrailResult.allow("No validators configured")

        for validator in self.validators:
            if not validator.enabled:
                continue
            current = validator.validate(tool_name, args, context)
            logger.debug(
                "ActionValidator %r: %s — %s",
                validator.name,
                current.action,
                current.reason,
            )
            if current.action == GuardrailAction.BLOCK:
                return current
            if current.action == GuardrailAction.CONFIRM:
                result = current  # keep going in case something blocks later

        return result

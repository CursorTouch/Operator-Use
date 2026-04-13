"""GuardrailRegistry: registration and lookup of guardrail instances.

The registry is the single source of truth for all active guardrails in a
running system. Guardrails self-register at construction time (opt-in via
:meth:`GuardrailRegistry.register`) or are registered explicitly.

Typical usage::

    registry = GuardrailRegistry()
    registry.register(BlockListValidator(blocked_tools={"shell"}))
    registry.register(KeywordBlockFilter(blocked_phrases={"drop table"}))

    validators = registry.get_all(ActionValidator)
    filters    = registry.get_all(ContentFilter)
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar, Type

from operator_use.guardrails.base import Guardrail, GuardrailResult

logger = logging.getLogger(__name__)

G = TypeVar("G", bound=Guardrail)


class GuardrailRegistry:
    """Central registry for guardrail instances.

    Guardrails are stored by name; registering a second guardrail with the
    same name replaces the first (last-write wins).
    """

    def __init__(self) -> None:
        self._guardrails: dict[str, Guardrail] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, guardrail: Guardrail) -> None:
        """Register ``guardrail`` under its :attr:`~Guardrail.name`.

        If a guardrail with the same name already exists it is replaced.
        """
        if guardrail.name in self._guardrails:
            logger.debug("GuardrailRegistry: replacing %r", guardrail.name)
        self._guardrails[guardrail.name] = guardrail
        logger.debug("GuardrailRegistry: registered %r", guardrail.name)

    def unregister(self, name: str) -> None:
        """Remove the guardrail identified by ``name``.

        Silently does nothing if the name is not registered.
        """
        self._guardrails.pop(name, None)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> Guardrail | None:
        """Return the guardrail with ``name``, or ``None`` if absent."""
        return self._guardrails.get(name)

    def get_all(self, guardrail_type: Type[G] | None = None) -> list[G]:
        """Return all registered guardrails, optionally filtered by type.

        Args:
            guardrail_type: If provided, only instances of this type are
                            returned.  Pass ``None`` to get everything.

        Returns:
            List of matching guardrail instances (enabled or not).
        """
        if guardrail_type is None:
            return list(self._guardrails.values())  # type: ignore[return-value]
        return [g for g in self._guardrails.values() if isinstance(g, guardrail_type)]

    def get_enabled(self, guardrail_type: Type[G] | None = None) -> list[G]:
        """Like :meth:`get_all` but only returns enabled guardrails."""
        return [g for g in self.get_all(guardrail_type) if g.enabled]

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def run_all(
        self,
        guardrail_type: Type[G] | None = None,
        context: dict[str, Any] | None = None,
    ) -> list[GuardrailResult]:
        """Run :meth:`~Guardrail.check` on every enabled guardrail of ``guardrail_type``.

        Returns all results (callers decide how to interpret them).
        """
        ctx = context or {}
        return [g.check(ctx) for g in self.get_enabled(guardrail_type)]

    def clear(self) -> None:
        """Remove all registered guardrails."""
        self._guardrails.clear()

    def __len__(self) -> int:
        return len(self._guardrails)

    def __repr__(self) -> str:
        names = list(self._guardrails.keys())
        return f"GuardrailRegistry(count={len(names)}, names={names})"

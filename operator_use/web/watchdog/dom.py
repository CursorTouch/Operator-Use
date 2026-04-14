from __future__ import annotations

from operator_use.web.browser.events import StateInvalidatedEvent
from operator_use.web.watchdog.base import BaseWatchdog


class DOMWatchdog(BaseWatchdog):
    """Marks browser state dirty when the DOM changes materially."""

    async def attach(self) -> None:
        self.session.on("DOM.documentUpdated", self._on_dom_updated)
        self.session.on("DOM.childNodeInserted", self._on_dom_changed)
        self.session.on("DOM.childNodeRemoved", self._on_dom_changed)
        self.session.on("DOM.attributeModified", self._on_dom_changed)
        self.session.on("DOM.attributeRemoved", self._on_dom_changed)

    def _emit_invalidated(self, session_id: str | None, reason: str) -> None:
        self.session.emit_browser_event(StateInvalidatedEvent(session_id=session_id, reason=reason))

    def _on_dom_updated(self, _event, session_id=None) -> None:
        self._emit_invalidated(session_id, "dom_document_updated")

    def _on_dom_changed(self, _event, session_id=None) -> None:
        self._emit_invalidated(session_id, "dom_mutation")

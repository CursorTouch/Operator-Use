from __future__ import annotations

import logging

from operator_use.web.browser.events import PopupOpenedEvent, StateInvalidatedEvent
from operator_use.web.watchdog.base import BaseWatchdog

logger = logging.getLogger(__name__)


class PopupWatchdog(BaseWatchdog):
    """Tracks newly opened tabs/popups and invalidates browser state."""

    async def attach(self) -> None:
        self.session.on("Target.targetCreated", self._on_target_created)
        self.session.on_browser_event(PopupOpenedEvent, self._on_popup_opened)

    def _on_target_created(self, event, _=None) -> None:
        info = event.get("targetInfo", {})
        if info.get("type") != "page":
            return
        opener_id = info.get("openerId")
        if not opener_id:
            return
        self.session.emit_browser_event(
            PopupOpenedEvent(
                session_id=None,
                target_id=info.get("targetId", ""),
                opener_id=opener_id,
                url=info.get("url", ""),
                title=info.get("title", ""),
            )
        )

    def _on_popup_opened(self, event: PopupOpenedEvent) -> None:
        logger.debug("PopupWatchdog detected popup/new tab: %s", event)
        self.session.emit_browser_event(
            StateInvalidatedEvent(session_id=event.session_id, reason="popup_opened")
        )

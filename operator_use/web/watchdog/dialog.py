from __future__ import annotations
import logging
from operator_use.web.watchdog.base import BaseWatchdog

logger = logging.getLogger(__name__)


class DialogWatchdog(BaseWatchdog):
    """Auto-dismisses JavaScript dialogs (alert/confirm/prompt).

    Without this, any alert() on a page blocks the entire renderer —
    no CDP commands go through until it is dismissed.
    """

    async def attach(self) -> None:
        self.session.on("Page.javascriptDialogOpening", self._on_dialog)

    async def _on_dialog(self, event, session_id=None) -> None:
        if not session_id:
            return
        dialog_type = event.get("type", "")
        message = event.get("message", "")
        logger.info("Auto-dismissing %s dialog: %s", dialog_type, message)
        try:
            await self.session.send(
                "Page.handleJavaScriptDialog",
                {"accept": True, "promptText": ""},
                session_id=session_id,
            )
        except Exception:
            pass

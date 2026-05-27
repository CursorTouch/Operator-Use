from __future__ import annotations
import logging
from operator_use.browser.watchdog.base import BaseWatchdog

logger = logging.getLogger(__name__)


class CrashWatchdog(BaseWatchdog):
    """Detects tab crashes and cleans up session state.

    Chrome sends Inspector.targetCrashed in the context of the crashed
    page's session. Without this the agent hangs indefinitely waiting
    for a response from a dead renderer.
    """

    async def attach(self) -> None:
        self.session.on('Inspector.targetCrashed', self._on_crash)

    def _on_crash(self, event, session_id=None) -> None:
        if not session_id:
            return

        target_id = self.session._session_manager.find_target_by_session(session_id)


        self.session._lifecycle.pop(session_id, None)
        self.session._page_loading.pop(session_id, None)
        ready = self.session._page_ready.pop(session_id, None)
        if ready:
            ready.set()

        if target_id:
            logger.warning('Tab crashed (target=%s, session=%s)', target_id, session_id)
            self.session._session_manager.remove_by_target(target_id)
            self.session._set_current_target_id(self.session._session_manager.current_target_id)
            if not self.session._sessions:
                self.session.crashed = True
        else:
            logger.debug('Sub-frame/worker crashed (session=%s) — ignored', session_id)

from __future__ import annotations

import asyncio
import logging
from time import monotonic

from operator_use.web.browser.events import (
    NavigationSettledEvent,
    NavigationStartedEvent,
    StateInvalidatedEvent,
)
from operator_use.web.browser.views import BrowserState
from operator_use.web.dom import DOM
from operator_use.web.watchdog.base import BaseWatchdog

logger = logging.getLogger(__name__)


class StateWatchdog(BaseWatchdog):
    """Coordinates stable browser state capture around navigation events."""

    def __init__(self, session) -> None:
        super().__init__(session)
        self._dirty = True
        self._last_stable_at = 0.0
        self._cached_state: BrowserState | None = None
        self._inflight_capture: asyncio.Task | None = None

    async def attach(self) -> None:
        self.session.on_browser_event(NavigationStartedEvent, self._on_navigation_started)
        self.session.on_browser_event(NavigationSettledEvent, self._on_navigation_settled)
        self.session.on_browser_event(StateInvalidatedEvent, self._on_state_invalidated)

    def _on_navigation_started(self, payload: NavigationStartedEvent) -> None:
        self._dirty = True
        self._cached_state = None
        logger.debug("StateWatchdog marked state dirty on navigation start: %s", payload)

    def _on_navigation_settled(self, payload: NavigationSettledEvent) -> None:
        self._dirty = True
        self._last_stable_at = monotonic()
        logger.debug("StateWatchdog saw navigation settle: %s", payload)

    def _on_state_invalidated(self, payload: StateInvalidatedEvent) -> None:
        self._dirty = True
        logger.debug("StateWatchdog invalidated state: %s", payload)

    async def get_state(self, use_vision: bool = False) -> BrowserState | None:
        if self.session._client is None or self.session._get_current_session_id() is None:
            return None
        if not self._dirty and self._cached_state is not None:
            return self._cached_state
        if self._inflight_capture is not None and not self._inflight_capture.done():
            return await self._inflight_capture
        self._inflight_capture = asyncio.create_task(self._capture_state(use_vision=use_vision))
        try:
            return await self._inflight_capture
        finally:
            if self._inflight_capture is not None and self._inflight_capture.done():
                self._inflight_capture = None

    async def _capture_state(self, use_vision: bool = False) -> BrowserState | None:
        if self.session._client is None or self.session._get_current_session_id() is None:
            return None

        if self.session.is_navigation_pending():
            await self.session._wait_for_page(
                timeout=self.session.config.maximum_wait_page_load_time
            )
            if self.session.is_navigation_pending():
                logger.debug("StateWatchdog skipped capture because navigation is still pending")
                return None

        min_wait = self.session.config.minimum_wait_page_load_time
        settle_wait = self.session.config.wait_for_network_idle_page_load_time
        if self._last_stable_at:
            elapsed = monotonic() - self._last_stable_at
            required_wait = max(min_wait, settle_wait)
            if elapsed < required_wait:
                await asyncio.sleep(required_wait - elapsed)

        page = self.session.current_page()
        dom = DOM(session=self.session)
        screenshot, dom_state = await dom.get_state(use_vision=use_vision)
        tabs = await self.session.get_all_tabs()
        current_tab = await self.session.get_current_tab()
        state = BrowserState(
            current_tab=current_tab,
            tabs=tabs,
            screenshot=screenshot or await page.get_screenshot(),
            dom_state=dom_state,
        )
        self.session._browser_state = state
        self._cached_state = state
        self._dirty = False
        return state

from unittest.mock import MagicMock

from operator_use.web.browser.events import PopupOpenedEvent
from operator_use.web.watchdog.popup import PopupWatchdog


def test_popup_watchdog_emits_popup_event_for_page_with_opener():
    session = MagicMock()
    watchdog = PopupWatchdog(session)

    watchdog._on_target_created(
        {
            "targetInfo": {
                "type": "page",
                "targetId": "target-1",
                "openerId": "target-0",
                "url": "https://example.com",
                "title": "Example",
            }
        }
    )

    emitted = session.emit_browser_event.call_args.args[0]
    assert isinstance(emitted, PopupOpenedEvent)
    assert emitted.target_id == "target-1"
    assert emitted.opener_id == "target-0"


def test_popup_watchdog_ignores_non_popup_targets():
    session = MagicMock()
    watchdog = PopupWatchdog(session)

    watchdog._on_target_created(
        {
            "targetInfo": {
                "type": "page",
                "targetId": "target-1",
            }
        }
    )

    session.emit_browser_event.assert_not_called()

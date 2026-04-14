from operator_use.web.browser.events import (
    NavigationSettledEvent,
    NavigationStartedEvent,
    StateInvalidatedEvent,
)


def test_browser_event_names_are_stable():
    assert NavigationStartedEvent.event_name() == "NavigationStartedEvent"
    assert NavigationSettledEvent.event_name() == "NavigationSettledEvent"
    assert StateInvalidatedEvent.event_name() == "StateInvalidatedEvent"


def test_navigation_settled_event_fields():
    event = NavigationSettledEvent(session_id="session-1", name="load")

    assert event.session_id == "session-1"
    assert event.name == "load"

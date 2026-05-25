from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserEvent:
    session_id: str | None

    @classmethod
    def event_name(cls) -> str:
        return cls.__name__


@dataclass(frozen=True)
class NavigationStartedEvent(BrowserEvent):
    pass


@dataclass(frozen=True)
class NavigationSettledEvent(BrowserEvent):
    name: str


@dataclass(frozen=True)
class StateInvalidatedEvent(BrowserEvent):
    reason: str


@dataclass(frozen=True)
class PopupOpenedEvent(BrowserEvent):
    target_id: str
    opener_id: str | None = None
    url: str = ""
    title: str = ""

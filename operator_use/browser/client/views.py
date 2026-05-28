from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from PIL.Image import Image

from operator_use.browser.dom.views import DOMState


@dataclass
class Tab:
    id: int
    url: str
    title: str
    target_id: str
    session_id: str

    def to_string(self) -> str:
        return f'{self.id} - Title: {self.title} - URL: {self.url}'


@dataclass
class BrowserState:
    current_tab: Tab | None = None
    tabs: list[Tab] = field(default_factory=list)
    screenshot: Union[Image, bytes, None] = None
    dom_state: DOMState = field(default_factory=DOMState)

    def tabs_to_string(self) -> str:
        return '\n'.join(tab.to_string() for tab in self.tabs)

    def to_string(self) -> str:
        """Compact, LLM-friendly summary of the full browser state."""
        parts: list[str] = []

        if self.current_tab:
            parts.append(f"Current tab: {self.current_tab.to_string()}")

        if self.tabs:
            parts.append(f"Open tabs:\n{self.tabs_to_string()}")

        interactive = self.dom_state.interactive_elements_to_string()
        if interactive and interactive != "No interactive elements":
            parts.append(f"Interactive elements:\n{interactive}")

        scrollable = self.dom_state.scrollable_elements_to_string()
        if scrollable and scrollable != "No scrollable elements":
            parts.append(f"Scrollable elements:\n{scrollable}")

        informative = self.dom_state.informative_elements_to_string()
        if informative:
            parts.append(f"Informative elements:\n{informative}")

        return "\n\n".join(parts) if parts else "Browser state unavailable."

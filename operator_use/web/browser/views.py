from dataclasses import dataclass, field
from operator_use.web.dom.views import DOMState


@dataclass
class Tab:
    id: int
    url: str
    title: str
    target_id: str
    session_id: str

    def to_string(self) -> str:
        return f"{self.id}|{self.title}|{self.url}"


@dataclass
class BrowserState:
    current_tab: Tab | None = None
    tabs: list[Tab] = field(default_factory=list)
    screenshot: bytes | None = None
    dom_state: DOMState = field(default_factory=DOMState)

    def tabs_to_string(self) -> str:
        if not self.tabs:
            return "No tabs open"
        header = "# id|title|url"
        rows = [header] + [tab.to_string() for tab in self.tabs]
        return "\n".join(rows)

    def to_string(self) -> str:
        return f"""## Browser State

Current Tab:
{self.current_tab.to_string() if self.current_tab else "No active tab"}

Open Tabs:
{self.tabs_to_string() if self.tabs else "No tabs open"}

Interactive Elements:
{self.dom_state.interactive_elements_to_string() if self.dom_state else "No interactive elements"}

Informative Elements:
{self.dom_state.informative_elements_to_string() if self.dom_state else "No informative elements"}

Scrollable Elements:
{self.dom_state.scrollable_elements_to_string() if self.dom_state else "No scrollable elements"}
"""

    def __str__(self) -> str:
        return self.to_string()

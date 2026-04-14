from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class Status(Enum):
    MAXIMIZED = "Maximized"
    MINIMIZED = "Minimized"
    NORMAL = "Normal"
    HIDDEN = "Hidden"


@dataclass
class Window:
    name: str
    status: Status
    x: int
    y: int
    width: int
    height: int
    handle: int  # X11 window ID (decimal)

    def to_row(self):
        return [self.name, self.status.value, self.width, self.height, hex(self.handle)]


@dataclass
class DesktopState:
    active_desktop: dict
    all_desktops: list[dict]
    active_window: Optional[Window]
    windows: list[Window] = field(default_factory=list)

    # ── formatters ────────────────────────────────────────────────────────

    def active_desktop_to_string(self):
        return self.active_desktop.get("name", "Desktop 0")

    def desktops_to_string(self):
        if not self.all_desktops:
            return "No desktops"
        header = "# name"
        rows = [header] + [d.get("name", "") for d in self.all_desktops]
        return "\n".join(rows)

    def active_window_to_string(self):
        if not self.active_window:
            return "No active window found"
        w = self.active_window
        return f"# name|status|width|height|handle\n{w.name}|{w.status.value}|{w.width}|{w.height}|{hex(w.handle)}"

    def windows_to_string(self):
        if not self.windows:
            return "No windows found"
        header = "# name|status|width|height|handle"
        rows = [header]
        for w in self.windows:
            rows.append(f"{w.name}|{w.status.value}|{w.width}|{w.height}|{hex(w.handle)}")
        return "\n".join(rows)

    def to_string(self):
        return f"""
## Desktop State

Active Desktop:
{self.active_desktop_to_string()}
All Desktops:
{self.desktops_to_string()}
Active Window:
{self.active_window_to_string()}
Opened Windows:
{self.windows_to_string()}

Interactive Elements:
No interactive elements (tree not yet implemented on Linux)

Scrollable Elements:
No scrollable elements (tree not yet implemented on Linux)
"""

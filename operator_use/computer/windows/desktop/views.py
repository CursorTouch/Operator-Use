from operator_use.computer.windows.tree.views import TreeState, BoundingBox
from dataclasses import dataclass
from typing import Optional
from PIL.Image import Image
from enum import Enum


class Browser(Enum):
    CHROME = "chrome"
    EDGE = "msedge"
    FIREFOX = "firefox"

    @classmethod
    def has_process(cls, process_name: str) -> bool:
        if not hasattr(cls, "_process_names"):
            cls._process_names = {f"{b.value}.exe" for b in cls}
        return process_name.lower() in cls._process_names


class Status(Enum):
    MAXIMIZED = "Maximized"
    MINIMIZED = "Minimized"
    NORMAL = "Normal"
    HIDDEN = "Hidden"


@dataclass
class Window:
    name: str
    is_browser: bool
    depth: int
    status: Status
    bounding_box: BoundingBox
    handle: int
    process_id: int

    def to_row(self):
        return [
            self.name,
            self.depth,
            self.status.value,
            self.bounding_box.width,
            self.bounding_box.height,
            self.handle,
        ]


@dataclass
class Size:
    width: int
    height: int

    def to_string(self):
        return f"({self.width},{self.height})"


@dataclass
class DesktopState:
    active_desktop: dict
    all_desktops: list[dict]
    windows: list[Window]
    active_window: Optional[Window]
    screenshot: Optional[Image] = None
    tree_state: Optional[TreeState] = None

    def active_desktop_to_string(self):
        return self.active_desktop.get("name", "")

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
        return f"# name|depth|status|width|height|handle\n{w.name}|{w.depth}|{w.status.value}|{w.bounding_box.width}|{w.bounding_box.height}|{w.handle}"

    def windows_to_string(self):
        if not self.windows:
            return "No windows found"
        header = "# name|depth|status|width|height|handle"
        rows = [header]
        for w in self.windows:
            rows.append(
                f"{w.name}|{w.depth}|{w.status.value}|{w.bounding_box.width}|{w.bounding_box.height}|{w.handle}"
            )
        return "\n".join(rows)

    def __str__(self):
        return self.to_string()

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
{self.tree_state.interactive_elements_to_string() if self.tree_state else "No interactive elements found"}

Scrollable Elements:
{self.tree_state.scrollable_elements_to_string() if self.tree_state else "No scrollable elements found"}
"""

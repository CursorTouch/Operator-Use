from operator_use.computer.macos.tree.views import BoundingBox
from operator_use.computer.macos.tree.views import TreeState
from dataclasses import dataclass
from PIL.Image import Image
from typing import Union
from enum import Enum


class Status(Enum):
    ACTIVE = "Active"  # Frontmost app with visible windows
    FULLSCREEN = "Fullscreen"  # Frontmost app in fullscreen mode
    VISIBLE = "Visible"  # Has windows on screen, not frontmost
    HIDDEN = "Hidden"  # Hidden via Cmd+H
    MINIMIZED = "Minimized"  # All windows minimized to Dock
    WINDOWLESS = "Windowless"  # Running but no windows


@dataclass
class Size:
    width: int
    height: int

    def to_string(self):
        return f"({self.width},{self.height})"


@dataclass
class Window:
    name: str
    is_browser: bool
    status: Status
    bounding_box: BoundingBox
    pid: int
    bundle_id: str


@dataclass
class DesktopState:
    active_window: Window | None
    windows: list[Window]
    screenshot: [Union[Image, bytes, None]] = None
    tree_state: TreeState | None = None

    def windows_to_string(self) -> str:
        if not self.windows:
            return "No open applications."
        header = "# name|bundle_id|status"
        rows = [header] + [f"{w.name}|{w.bundle_id}|{w.status.value}" for w in self.windows]
        return "\n".join(rows)

    def active_window_to_string(self) -> str:
        if self.active_window is None:
            return "No focused window."
        w = self.active_window
        return f"{w.name}|{w.bundle_id}|{w.status.value}"

    def to_string(self) -> str:
        return f"""
## Desktop State

Active Window:
{self.active_window_to_string()}
Opened Windows:
{self.windows_to_string()}

Interactive Elements:
{self.tree_state.interactive_elements_to_string() if self.tree_state else "No interactive elements found"}

Scrollable Elements:
{self.tree_state.scrollable_elements_to_string() if self.tree_state else "No scrollable elements found"}
"""

from program.computer.macos.tree.views import BoundingBox, TreeState
from dataclasses import dataclass
from PIL.Image import Image
from typing import Union
from enum import Enum


class Browser(Enum):
    """Supported browser applications kept for backwards compatibility."""

    SAFARI = 'com.apple.Safari'
    CHROME = 'com.google.Chrome'
    FIREFOX = 'org.mozilla.firefox'
    EDGE = 'com.microsoft.edgemac'

    @classmethod
    def has_bundle_id(cls, bundle_id: str) -> bool:
        """Check if a bundle ID matches a known browser."""
        if not hasattr(cls, '_bundle_ids'):
            cls._bundle_ids = {b.value for b in cls}
        return bundle_id in cls._bundle_ids


class Status(Enum):
    ACTIVE = 'Active'           # Frontmost app with visible windows
    FULLSCREEN = 'Fullscreen'   # Frontmost app in fullscreen mode
    VISIBLE = 'Visible'         # Has windows on screen, not frontmost
    HIDDEN = 'Hidden'           # Hidden via Cmd+H
    MINIMIZED = 'Minimized'     # All windows minimized to Dock
    WINDOWLESS = 'Windowless'   # Running but no windows


@dataclass
class Size:
    width: int
    height: int

    def to_string(self):
        return f'({self.width},{self.height})'


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
    screenshot: Union[Image, bytes, None] = None
    tree_state: TreeState | None = None

    def windows_to_string(self) -> str:
        """Format windows list for display."""
        if not self.windows:
            return "No open applications."
        lines = [f"{w.name} ({w.bundle_id}) - {w.status.value}" for w in self.windows]
        return "\n".join(lines)

    def active_window_to_string(self) -> str:
        """Format active window for display."""
        if self.active_window is None:
            return "No focused window."
        w = self.active_window
        return f"{w.name} ({w.bundle_id}) - {w.status.value}"

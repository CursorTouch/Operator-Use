"""Abstract base types for the computer / desktop control layer.

Platform implementations (macos, windows, linux) each provide a concrete
``Desktop`` subclass.  ``operator_use.computer.__init__`` selects the right
one at import time based on ``sys.platform``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional, Tuple, Union

from PIL.Image import Image


# ---------------------------------------------------------------------------
# Shared value types
# ---------------------------------------------------------------------------

@dataclass
class Size:
    """Screen or window dimensions in logical pixels."""
    width: int
    height: int

    def to_string(self) -> str:
        return f"({self.width},{self.height})"


class WindowStatus(str, Enum):
    """Normalised window visibility / focus state across platforms."""
    ACTIVE      = "Active"       # Frontmost, focused
    FULLSCREEN  = "Fullscreen"   # Occupies the entire display
    VISIBLE     = "Visible"      # On screen but not focused
    MINIMIZED   = "Minimized"    # Collapsed to taskbar / Dock
    HIDDEN      = "Hidden"       # Programmatically hidden
    WINDOWLESS  = "Windowless"   # Running process with no visible window


@dataclass
class Window:
    """Platform-agnostic window descriptor."""
    name: str
    is_browser: bool
    status: WindowStatus
    x: int
    y: int
    width: int
    height: int
    process_id: int


@dataclass
class DesktopState:
    """Snapshot of the desktop returned by ``Desktop.get_state()``.

    ``screenshot`` is ``None`` when ``use_vision=False`` was requested.
    ``tree_state`` is ``None`` when ``use_accessibility=False`` was requested
    or when the platform does not support an accessibility tree.
    """
    active_window: Optional[Window]
    windows: list[Window] = field(default_factory=list)
    screenshot: Union[Image, bytes, None] = None
    tree_state: Optional[object] = None   # platform-specific TreeState

    def windows_to_string(self) -> str:
        """Human-readable list of open windows."""
        if not self.windows:
            return "No open applications."
        return "\n".join(
            f"{w.name} [{w.status.value}] pid={w.process_id}"
            for w in self.windows
        )

    def active_window_to_string(self) -> str:
        """Human-readable active window summary."""
        if self.active_window is None:
            return "No focused window."
        w = self.active_window
        return f"{w.name} [{w.status.value}] pid={w.process_id}"


# ---------------------------------------------------------------------------
# Abstract Desktop interface
# ---------------------------------------------------------------------------

class Desktop(ABC):
    """Abstract desktop controller.

    Every platform subclass must implement the methods below.  The
    ``ComputerTool`` interacts exclusively through this interface so that
    tool logic stays platform-neutral.
    """

    # ------------------------------------------------------------------
    # State / inspection
    # ------------------------------------------------------------------

    @abstractmethod
    def get_state(
        self,
        use_vision: bool = False,
        as_bytes: bool = False,
    ) -> DesktopState:
        """Return a full snapshot of the current desktop.

        Args:
            use_vision: When ``True``, capture a screenshot and include it
                        in the returned ``DesktopState``.
            as_bytes:   When ``True`` (and ``use_vision`` is ``True``),
                        encode the screenshot as raw PNG bytes instead of a
                        ``PIL.Image``.
        """

    @abstractmethod
    def get_screen_size(self) -> Size:
        """Return the combined virtual screen size in logical pixels."""

    @abstractmethod
    def get_windows(self) -> list[Window]:
        """Return all currently visible / open windows."""

    @abstractmethod
    def get_foreground_window(self) -> Optional[Window]:
        """Return the window that currently has keyboard focus, or ``None``."""

    @abstractmethod
    def get_screenshot(self, as_bytes: bool = False) -> Union[Image, bytes]:
        """Capture the full screen.

        Args:
            as_bytes: Return PNG bytes instead of a ``PIL.Image``.
        """

    # ------------------------------------------------------------------
    # Pointer — click, move, scroll, drag
    # ------------------------------------------------------------------

    @abstractmethod
    def click(
        self,
        loc: Tuple[int, int],
        button: Literal["left", "right", "middle"] = "left",
        clicks: int = 1,
    ) -> None:
        """Click at screen coordinate *loc*.

        Args:
            loc:     ``(x, y)`` in logical pixels.
            button:  Mouse button to use.
            clicks:  Number of successive clicks (1 = single, 2 = double, …).
                     Pass 0 to only move the pointer without clicking.
        """

    @abstractmethod
    def move(self, loc: Tuple[int, int]) -> None:
        """Move the pointer to *loc* without clicking."""

    @abstractmethod
    def drag(self, loc: Tuple[int, int]) -> None:
        """Drag from the current pointer position to *loc*."""

    @abstractmethod
    def scroll(
        self,
        loc: Optional[Tuple[int, int]],
        orientation: Literal["vertical", "horizontal"] = "vertical",
        direction: Literal["up", "down", "left", "right"] = "down",
        wheel_times: int = 1,
    ) -> None:
        """Scroll at *loc* (or the current pointer position when ``None``).

        Args:
            loc:          Target coordinate, or ``None`` to scroll at the
                          current pointer position.
            orientation:  Scroll axis.
            direction:    Scroll direction.
            wheel_times:  Number of discrete scroll wheel ticks.
        """

    # ------------------------------------------------------------------
    # Keyboard — type, shortcut
    # ------------------------------------------------------------------

    @abstractmethod
    def type(
        self,
        loc: Tuple[int, int],
        text: str,
        caret_position: Literal["start", "idle", "end"] = "idle",
        clear: bool = False,
        press_enter: bool = False,
    ) -> None:
        """Click *loc* then type *text* into the focused field.

        Args:
            loc:             Target coordinate to click before typing.
            text:            The text to insert.
            caret_position:  Where to move the caret before typing:
                             ``"start"`` → home, ``"end"`` → end,
                             ``"idle"`` → leave as-is.
            clear:           Select-all and delete existing content first.
            press_enter:     Press Enter after typing.
        """

    @abstractmethod
    def shortcut(self, shortcut: str) -> None:
        """Press a keyboard shortcut such as ``"command+c"`` or ``"ctrl+z"``."""

    # ------------------------------------------------------------------
    # Application management
    # ------------------------------------------------------------------

    @abstractmethod
    def app(
        self,
        mode: Literal["launch", "switch", "resize", "move"] = "launch",
        name: Optional[str] = None,
        loc: Optional[Tuple[int, int]] = None,
        size: Optional[Tuple[int, int]] = None,
    ) -> str:
        """Manage applications.

        Args:
            mode:  ``"launch"`` — start the app by name or bundle/app ID.
                   ``"switch"`` — bring the running app to the foreground.
                   ``"resize"`` — resize the frontmost window to *size*.
                   ``"move"``   — move the frontmost window to *loc*.
            name:  App name or bundle / process ID.  Required for launch and
                   switch.
            loc:   Target top-left corner ``(x, y)`` for move.
            size:  Target dimensions ``(width, height)`` for resize.

        Returns:
            A human-readable status string.
        """

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @abstractmethod
    def wait(self, duration: float) -> None:
        """Block for *duration* seconds."""

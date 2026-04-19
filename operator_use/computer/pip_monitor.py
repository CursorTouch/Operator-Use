"""Picture-in-Picture agent monitor overlay.

A floating always-on-top window that captures the agent's active window at 2fps
and renders it as a 320x240 thumbnail so the user can observe the agent without
switching macOS Spaces or virtual desktops.

Dependencies are fully optional — import this module even without PySide6:

    pip install operator-use[pip-monitor]
"""

from __future__ import annotations

import subprocess
import sys
import threading
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional PySide6 import
# ---------------------------------------------------------------------------

try:
    from PySide6.QtWidgets import QApplication, QLabel, QWidget
    from PySide6.QtCore import Qt, QTimer, QSize
    from PySide6.QtGui import QPixmap, QScreen

    _PYSIDE6_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PYSIDE6_AVAILABLE = False

# ---------------------------------------------------------------------------
# Platform window-ID lookup
# ---------------------------------------------------------------------------

_CAPTURE_INTERVAL_MS = 500  # 2 fps
_PIP_WIDTH = 320
_PIP_HEIGHT = 240


def _find_window_id(title: str) -> Optional[int]:
    """Return an integer window-ID for the first window matching *title*.

    Returns ``None`` if the window cannot be found or the platform helper is
    unavailable.
    """
    platform = sys.platform

    if platform == "linux":
        return _find_window_id_linux(title)
    elif platform == "darwin":
        return _find_window_id_macos(title)
    elif platform == "win32":
        return _find_window_id_windows(title)

    return None


def _find_window_id_linux(title: str) -> Optional[int]:
    """Use ``xdotool`` to resolve a window ID by title on Linux."""
    try:
        result = subprocess.run(
            ["xdotool", "search", "--name", title],
            capture_output=True,
            text=True,
            timeout=3,
        )
        lines = result.stdout.strip().splitlines()
        if lines:
            return int(lines[0])
    except Exception as exc:
        logger.debug("xdotool lookup failed: %s", exc)
    return None


def _find_window_id_macos(title: str) -> Optional[int]:
    """Use ``CGWindowListCopyWindowInfo`` via ctypes to find a window on macOS."""
    try:
        import ctypes
        import ctypes.util

        core_graphics = ctypes.cdll.LoadLibrary(
            ctypes.util.find_library("CoreGraphics") or "CoreGraphics"
        )

        # kCGWindowListOptionAll = 0, kCGNullWindowID = 0
        window_list = core_graphics.CGWindowListCopyWindowInfo(0, 0)
        if not window_list:
            return None

        # Use CoreFoundation to iterate the CFArray
        cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation") or "CoreFoundation")

        count = cf.CFArrayGetCount(window_list)
        for i in range(count):
            item = cf.CFArrayGetValueAtIndex(window_list, i)
            # Read kCGWindowName key
            key = cf.CFStringCreateWithCString(None, b"kCGWindowName", 0x08000100)
            value = cf.CFDictionaryGetValue(item, key)
            if not value:
                cf.CFRelease(key)
                continue

            buf = ctypes.create_string_buffer(512)
            cf.CFStringGetCString(value, buf, 512, 0x08000100)
            window_name = buf.value.decode("utf-8", errors="replace")
            cf.CFRelease(key)

            if title.lower() in window_name.lower():
                # Read kCGWindowNumber
                num_key = cf.CFStringCreateWithCString(None, b"kCGWindowNumber", 0x08000100)
                num_val = cf.CFDictionaryGetValue(item, num_key)
                wid = ctypes.c_int64(0)
                cf.CFNumberGetValue(num_val, 4, ctypes.byref(wid))
                cf.CFRelease(num_key)
                cf.CFRelease(window_list)
                return int(wid.value)

        cf.CFRelease(window_list)
    except Exception as exc:
        logger.debug("CGWindowListCopyWindowInfo lookup failed: %s", exc)
    return None


def _find_window_id_windows(title: str) -> Optional[int]:
    """Use ``FindWindow`` via ctypes to locate a window by title on Windows."""
    try:
        import ctypes

        hwnd = ctypes.windll.user32.FindWindowW(None, title)
        if hwnd:
            return int(hwnd)
    except Exception as exc:
        logger.debug("FindWindow lookup failed: %s", exc)
    return None


# ---------------------------------------------------------------------------
# PiP overlay widget
# ---------------------------------------------------------------------------

if _PYSIDE6_AVAILABLE:

    class _PiPWindow(QWidget):
        """Frameless, always-on-top overlay that renders the agent window."""

        def __init__(self, source_title: str) -> None:
            super().__init__(
                None,
                Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint,
            )
            self._source_title = source_title
            self._label = QLabel(self)
            self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._label.resize(_PIP_WIDTH, _PIP_HEIGHT)

            self.setFixedSize(_PIP_WIDTH, _PIP_HEIGHT)
            self.setWindowOpacity(0.88)
            self._place_top_right()

            self._timer = QTimer(self)
            self._timer.setInterval(_CAPTURE_INTERVAL_MS)
            self._timer.timeout.connect(self._refresh)
            self._timer.start()

        def _place_top_right(self) -> None:
            """Position the window in the top-right corner of the primary screen."""
            screen: QScreen = QApplication.primaryScreen()
            geom = screen.availableGeometry()
            x = geom.right() - _PIP_WIDTH
            y = geom.top()
            self.move(x, y)

        def update_source(self, title: str) -> None:
            self._source_title = title

        def _refresh(self) -> None:
            """Grab the source window and update the label pixmap."""
            wid = _find_window_id(self._source_title)
            if wid is None:
                return

            screen: QScreen = QApplication.primaryScreen()
            pixmap: QPixmap = screen.grabWindow(wid)
            if pixmap.isNull():
                return

            scaled = pixmap.scaled(
                QSize(_PIP_WIDTH, _PIP_HEIGHT),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._label.setPixmap(scaled)

        def closeEvent(self, event) -> None:  # noqa: N802
            self._timer.stop()
            super().closeEvent(event)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class PiPMonitor:
    """Floating picture-in-picture monitor for an agent's active window.

    Usage::

        monitor = PiPMonitor()
        monitor.start("Agent Window Title")
        # ... agent runs ...
        monitor.stop()
    """

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._app: Optional[object] = None
        self._window: Optional[object] = None
        self._source_title: str = ""

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def start(self, source_window_title: str) -> None:
        """Launch the PiP overlay in a daemon thread.

        Does nothing (logs a warning) when PySide6 is not installed.
        """
        if not _PYSIDE6_AVAILABLE:
            logger.warning(
                "PiPMonitor: PySide6 is not installed. "
                "Install it with: pip install operator-use[pip-monitor]"
            )
            return

        if self._running.is_set():
            logger.warning("PiPMonitor: already running — call stop() first.")
            return

        self._source_title = source_window_title
        self._running.set()

        self._thread = threading.Thread(
            target=self._run_event_loop,
            args=(source_window_title,),
            daemon=True,
            name="pip-monitor",
        )
        self._thread.start()

    def stop(self) -> None:
        """Close the PiP window and join the daemon thread."""
        self._running.clear()

        if self._window is not None:
            try:
                self._window.close()
            except Exception as exc:
                logger.debug("PiPMonitor: error closing window: %s", exc)
            self._window = None

        if self._app is not None:
            try:
                self._app.quit()
            except Exception as exc:
                logger.debug("PiPMonitor: error quitting app: %s", exc)
            self._app = None

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None

    def update_source(self, window_title: str) -> None:
        """Change the monitored window title (takes effect on next capture)."""
        self._source_title = window_title
        if self._window is not None:
            try:
                self._window.update_source(window_title)
            except Exception as exc:
                logger.debug("PiPMonitor: error updating source: %s", exc)

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """True if the overlay is currently active."""
        return self._running.is_set()

    @property
    def source_title(self) -> str:
        """The window title currently being monitored."""
        return self._source_title

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_event_loop(self, source_title: str) -> None:
        """Run the Qt event loop inside the daemon thread."""
        if not _PYSIDE6_AVAILABLE:
            return

        app = QApplication.instance() or QApplication(sys.argv)
        self._app = app

        window = _PiPWindow(source_title)
        self._window = window
        window.show()

        app.exec()


# ---------------------------------------------------------------------------
# Module-level availability check
# ---------------------------------------------------------------------------


def is_available() -> bool:
    """Return True when PySide6 is installed and the overlay can be used."""
    return _PYSIDE6_AVAILABLE

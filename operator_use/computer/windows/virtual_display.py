"""Windows Virtual Display Manager — Parsec VDD confinement layer.

Creates a dedicated virtual monitor for the agent using the Parsec Virtual
Display Driver (VDD), providing spatial isolation at the display level.
Agent windows live on a separate monitor the user never sees.

Requires Parsec Virtual Display Driver: https://github.com/nomi-san/parsec-vdd

Installation:
    Download and install ParsecVDA from the repository above, or via the
    Parsec desktop application (Settings → Displays → Enable Virtual Display).

Usage::

    mgr = VirtualDisplayManager()
    if mgr.is_available():
        mgr.create_virtual_display(width=1920, height=1080, refresh_rate=60)
        mgr.move_window_to_virtual_display("Notepad")
        # ... agent does work ...
        mgr.remove_virtual_display()

All public methods return False / None on failure rather than raising, so the
caller can treat VDD as an optional enhancement without defensive try/except.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Win32 constants (kept at module level so tests can patch them easily)
# ---------------------------------------------------------------------------

SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_SHOWWINDOW = 0x0040

MONITOR_DEFAULTTONEAREST = 0x00000002

# ---------------------------------------------------------------------------
# Lazy ctypes imports — succeed on any platform; actual Win32 calls are
# guarded behind sys.platform checks at call time.
# ---------------------------------------------------------------------------

_user32: Any = None
_kernel32: Any = None
_ctypes_loaded = False


def _load_win32() -> bool:
    """Attempt to load user32 / kernel32 via ctypes.  Returns True on success."""
    global _user32, _kernel32, _ctypes_loaded
    if _ctypes_loaded:
        return _user32 is not None
    _ctypes_loaded = True
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        _user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        _kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        return True
    except Exception as exc:
        logger.debug("ctypes win32 load failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Registry helper — thin wrapper so tests can mock winreg cleanly
# ---------------------------------------------------------------------------


def _registry_key_exists(hive: int | None, subkey: str) -> bool:
    """Return True if *subkey* exists under *hive* in the registry.

    Returns False on ImportError (non-Windows), OSError (key missing), or if
    *hive* is None.
    """
    if hive is None:
        return False
    try:
        import winreg  # type: ignore[import]

        handle = winreg.OpenKey(hive, subkey)
        winreg.CloseKey(handle)
        return True
    except (ImportError, OSError, FileNotFoundError):
        return False


# ---------------------------------------------------------------------------
# VirtualDisplayManager
# ---------------------------------------------------------------------------


class VirtualDisplayManager:
    """Windows-only plugin that manages a Parsec Virtual Display Driver monitor.

    All methods are safe to call on non-Windows platforms and when VDD is not
    installed — they return ``False`` or ``None`` without raising exceptions.
    """

    # Registry path written by the Parsec VDA installer
    _VDD_REGISTRY_KEY = r"SOFTWARE\Parsec\vdd"
    # Driver INF file name (alternative detection path)
    _VDD_INF_NAME = "ParsecVDA.inf"
    # Executable name for subprocess fallback
    _VDD_EXE = "ParsecVDA.exe"

    def __init__(self) -> None:
        self._virtual_monitor_index: int | None = None
        self._virtual_monitor_handle: int | None = None

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True only on win32 with the Parsec VDD installed."""
        if sys.platform != "win32":
            return False
        return self.is_vdd_installed()

    def is_vdd_installed(self) -> bool:
        """Check whether the Parsec VDD is present on this machine.

        Tries two detection strategies in order:
        1. Registry key ``HKLM\\SOFTWARE\\Parsec\\vdd``
        2. Presence of ``ParsecVDA.inf`` in the Windows driver store

        Returns True if either check succeeds.
        """
        try:
            import winreg  # type: ignore[import]

            hklm: int | None = winreg.HKEY_LOCAL_MACHINE
        except ImportError:
            # Not on Windows at all; _registry_key_exists handles None gracefully.
            hklm = None

        # Strategy 1 — registry
        if _registry_key_exists(hklm, self._VDD_REGISTRY_KEY):
            logger.debug("Parsec VDD detected via registry key")
            return True

        # Strategy 2 — driver store
        try:
            result = subprocess.run(
                ["pnputil", "/enum-drivers", "/class", "Display"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if self._VDD_INF_NAME.lower() in result.stdout.lower():
                logger.debug("Parsec VDD detected via driver store (%s)", self._VDD_INF_NAME)
                return True
        except Exception as exc:
            logger.debug("pnputil driver check failed: %s", exc)

        return False

    # ------------------------------------------------------------------
    # Display lifecycle
    # ------------------------------------------------------------------

    def create_virtual_display(
        self,
        width: int = 1920,
        height: int = 1080,
        refresh_rate: int = 60,
    ) -> bool:
        """Add a virtual monitor via the Parsec VDA API.

        Tries ``ctypes.windll.ParsecVDA`` first; falls back to launching
        ``ParsecVDA.exe add`` as a subprocess.  Returns True on success.
        """
        if sys.platform != "win32":
            logger.debug("create_virtual_display: not on win32, skipping")
            return False

        # Attempt ctypes direct call first
        if self._create_via_ctypes(width, height, refresh_rate):
            return True

        # Subprocess fallback
        return self._create_via_subprocess(width, height, refresh_rate)

    def _create_via_ctypes(self, width: int, height: int, refresh_rate: int) -> bool:
        """Try to call ParsecVDA.dll directly via ctypes."""
        try:
            import ctypes

            vda = ctypes.windll.ParsecVDA  # type: ignore[attr-defined]
            # ParsecVDAAddDisplay(width, height, hz) — unofficial API
            result = vda.ParsecVDAAddDisplay(
                ctypes.c_int(width),
                ctypes.c_int(height),
                ctypes.c_int(refresh_rate),
            )
            if result == 0:
                logger.info(
                    "Virtual display created via ctypes (%dx%d@%dHz)", width, height, refresh_rate
                )
                self._virtual_monitor_index = 0
                return True
            logger.debug("ParsecVDAAddDisplay returned %s", result)
        except (AttributeError, OSError, Exception) as exc:
            logger.debug("ctypes VDA call failed: %s", exc)
        return False

    def _create_via_subprocess(self, width: int, height: int, refresh_rate: int) -> bool:
        """Launch ParsecVDA.exe as a subprocess fallback."""
        try:
            result = subprocess.run(
                [
                    self._VDD_EXE,
                    "add",
                    f"--width={width}",
                    f"--height={height}",
                    f"--hz={refresh_rate}",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                logger.info(
                    "Virtual display created via subprocess (%dx%d@%dHz)",
                    width,
                    height,
                    refresh_rate,
                )
                self._virtual_monitor_index = 0
                return True
            logger.debug("ParsecVDA.exe add returned %d: %s", result.returncode, result.stderr)
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as exc:
            logger.debug("Subprocess VDA call failed: %s", exc)
        return False

    def remove_virtual_display(self) -> bool:
        """Remove the virtual display created by this manager instance.

        Returns True on success, False if removal failed or no display was
        previously created.
        """
        if sys.platform != "win32":
            return False
        if self._virtual_monitor_index is None:
            logger.debug("remove_virtual_display: no virtual display to remove")
            return False

        removed = self._remove_via_ctypes() or self._remove_via_subprocess()
        if removed:
            self._virtual_monitor_index = None
            self._virtual_monitor_handle = None
        return removed

    def _remove_via_ctypes(self) -> bool:
        try:
            import ctypes

            vda = ctypes.windll.ParsecVDA  # type: ignore[attr-defined]
            result = vda.ParsecVDARemoveDisplay(ctypes.c_int(self._virtual_monitor_index or 0))
            if result == 0:
                logger.info("Virtual display removed via ctypes")
                return True
            logger.debug("ParsecVDARemoveDisplay returned %s", result)
        except (AttributeError, OSError, Exception) as exc:
            logger.debug("ctypes VDA remove failed: %s", exc)
        return False

    def _remove_via_subprocess(self) -> bool:
        try:
            result = subprocess.run(
                [self._VDD_EXE, "remove"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                logger.info("Virtual display removed via subprocess")
                return True
            logger.debug("ParsecVDA.exe remove returned %d: %s", result.returncode, result.stderr)
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as exc:
            logger.debug("Subprocess VDA remove failed: %s", exc)
        return False

    # ------------------------------------------------------------------
    # Window placement
    # ------------------------------------------------------------------

    def move_window_to_virtual_display(self, window_title: str) -> bool:
        """Move a window to the virtual monitor.

        Uses ``EnumWindows`` to find the target HWND by title, then
        ``EnumDisplayMonitors`` to locate the virtual monitor handle, and
        finally ``SetWindowPos`` to move the window.

        Returns True if the window was successfully moved.
        """
        if sys.platform != "win32":
            return False
        if not _load_win32():
            return False

        hwnd = self._find_window(window_title)
        if hwnd is None:
            logger.debug("move_window_to_virtual_display: window %r not found", window_title)
            return False

        monitor = self._get_virtual_monitor()
        if monitor is None:
            logger.debug("move_window_to_virtual_display: virtual monitor not found")
            return False

        return self._set_window_pos(hwnd, monitor)

    def _find_window(self, title: str) -> int | None:
        """Return the HWND of the first visible top-level window whose title
        contains *title* (case-insensitive).  Returns None if not found."""
        if _user32 is None:
            return None
        try:
            import ctypes
            from ctypes.wintypes import HWND, LPARAM

            found: list[int] = []
            title_lower = title.lower()

            EnumWindowsProc = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]
                ctypes.c_bool, HWND, LPARAM
            )

            def _callback(hwnd: int, _lparam: int) -> bool:
                buf = ctypes.create_unicode_buffer(512)
                _user32.GetWindowTextW(hwnd, buf, 512)
                if title_lower in buf.value.lower():
                    found.append(hwnd)
                    return False  # stop enumeration
                return True

            _user32.EnumWindows(EnumWindowsProc(_callback), 0)
            return found[0] if found else None
        except Exception as exc:
            logger.debug("_find_window failed: %s", exc)
            return None

    def _get_virtual_monitor(self) -> Any | None:
        """Return the HMONITOR for the virtual display.

        Uses ``EnumDisplayMonitors`` and picks the last monitor (heuristic:
        the virtual display is typically the last one enumerated after the
        physical displays).
        """
        if _user32 is None:
            return None
        try:
            import ctypes
            from ctypes.wintypes import HDC, LPRECT

            monitors: list[Any] = []

            MonitorEnumProc = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]
                ctypes.c_bool,
                ctypes.c_ulong,
                HDC,
                LPRECT,
                ctypes.c_double,
            )

            def _mon_callback(hmon: Any, _hdc: Any, _rect: Any, _data: Any) -> bool:
                monitors.append(hmon)
                return True

            _user32.EnumDisplayMonitors(None, None, MonitorEnumProc(_mon_callback), 0)

            # Virtual display is the last monitor; need at least 2 (physical + virtual)
            if len(monitors) >= 2:
                self._virtual_monitor_handle = monitors[-1]
                return monitors[-1]
            logger.debug("_get_virtual_monitor: only %d monitor(s) found", len(monitors))
        except Exception as exc:
            logger.debug("_get_virtual_monitor failed: %s", exc)
        return None

    def _set_window_pos(self, hwnd: int, monitor: Any) -> bool:
        """Move *hwnd* onto *monitor* using ``SetWindowPos``."""
        if _user32 is None:
            return False
        try:
            import ctypes

            # MONITORINFO layout: cbSize(4) + rcMonitor(16) + rcWork(16) + dwFlags(4)
            # We pack it as an array of 10 LONGs for simplicity.
            moninfo_array = (ctypes.c_long * 10)()
            moninfo_array[0] = 40  # cbSize
            _user32.GetMonitorInfoW(monitor, ctypes.byref(moninfo_array))
            # rcMonitor is at offset 4 (4 LONGs = left, top, right, bottom)
            left = moninfo_array[1]
            top = moninfo_array[2]

            flags = SWP_NOSIZE | SWP_NOZORDER | SWP_SHOWWINDOW
            ret = _user32.SetWindowPos(hwnd, None, left, top, 0, 0, flags)
            if ret:
                logger.info("Window HWND=%d moved to virtual monitor (%d, %d)", hwnd, left, top)
                return True
            logger.debug("SetWindowPos returned 0 for HWND=%d", hwnd)
        except Exception as exc:
            logger.debug("_set_window_pos failed: %s", exc)
        return False

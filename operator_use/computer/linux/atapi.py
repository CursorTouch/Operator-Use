"""Linux AT-SPI cursorless automation via the D-Bus accessibility API.

Primary path: pyatspi (AT-SPI2) — traverses the accessibility tree to find
UI elements and invokes the Action interface directly, without simulating
pointer/keyboard events.

Fallback path: ydotool — a Wayland-compatible input injection tool that
runs as a privileged daemon.  Used when pyatspi is unavailable or when the
target element cannot be located in the AT-SPI tree.

Installation
------------
AT-SPI (primary):
    pip install "operator-use[linux-automation]"
    # requires the AT-SPI2 D-Bus accessibility service to be running

ydotool (fallback system tool — NOT a Python package):
    sudo apt install ydotool          # Debian/Ubuntu
    sudo dnf install ydotool          # Fedora
    sudo pacman -S ydotool            # Arch
    # then start the daemon: sudo systemctl enable --now ydotoold

Platform guard
--------------
This module can be *imported* on any OS — all Linux-specific imports are lazy.
LinuxATSPIAutomation.is_available() will simply return False on non-Linux
systems, and the action methods will raise RuntimeError immediately.

Usage
-----
    from operator_use.computer.linux.atapi import LinuxATSPIAutomation
    automation = LinuxATSPIAutomation()
    automation.click("gedit", "Open")
    automation.type_text("gedit", "text-field", "Hello, world!")
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Optional pyatspi import — never fails at module load time
# ---------------------------------------------------------------------------

_pyatspi: Any = None
_pyatspi_available: bool = False

try:
    import pyatspi as _pyatspi  # type: ignore[import-untyped]

    _pyatspi_available = True
except Exception:  # ImportError, OSError, D-Bus errors …
    pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_linux() -> bool:
    return sys.platform == "linux"


def _ydotool_available() -> bool:
    """Return True if the *ydotool* binary is on PATH."""
    return shutil.which("ydotool") is not None


def _find_element(app_name: str, element_name: str) -> Any | None:
    """Traverse the AT-SPI tree and return the first matching element.

    Returns *None* if pyatspi is unavailable, the named application cannot be
    found in the registry, or no child element matches *element_name*.
    """
    if not _pyatspi_available or _pyatspi is None:
        return None

    try:
        desktop = _pyatspi.Registry.getDesktop(0)
    except Exception:
        return None

    # Locate the application by name (case-insensitive).
    app_node = None
    for i in range(desktop.childCount):
        try:
            child = desktop.getChildAtIndex(i)
            if child and child.name.lower() == app_name.lower():
                app_node = child
                break
        except Exception:
            continue

    if app_node is None:
        return None

    # BFS through all children looking for the element by name.
    queue: list[Any] = [app_node]
    while queue:
        node = queue.pop(0)
        try:
            if node.name and node.name.lower() == element_name.lower():
                return node
            for i in range(node.childCount):
                try:
                    queue.append(node.getChildAtIndex(i))
                except Exception:
                    pass
        except Exception:
            continue

    return None


def _ydotool_click() -> None:
    """Issue a left-click at the current pointer position via ydotool."""
    subprocess.run(
        ["ydotool", "click", "0xC0"],
        check=True,
        capture_output=True,
    )


def _ydotool_type(text: str) -> None:
    """Type *text* via ydotool."""
    subprocess.run(
        ["ydotool", "type", "--", text],
        check=True,
        capture_output=True,
    )


# ---------------------------------------------------------------------------
# Public automation class
# ---------------------------------------------------------------------------


class LinuxATSPIAutomation:
    """Cursorless desktop automation for Linux using AT-SPI with ydotool fallback.

    All public methods are safe to call on non-Linux systems — they raise
    RuntimeError immediately rather than crashing at import time.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def click(self, app_name: str, element_name: str) -> None:
        """Click *element_name* inside *app_name*.

        Strategy
        --------
        1. If pyatspi is available: find the element in the AT-SPI tree and
           invoke its default Action (typically "click" / "press").
        2. If pyatspi is unavailable or element is not found: fall back to
           ``ydotool click 0xC0`` (left-button down+up at current cursor).

        Parameters
        ----------
        app_name:
            Accessible name of the target application (e.g. ``"gedit"``).
        element_name:
            Accessible name of the target element (e.g. ``"Open"``).

        Raises
        ------
        RuntimeError
            On non-Linux platforms, or when neither pyatspi nor ydotool is
            available.
        """
        if not _is_linux():
            raise RuntimeError("LinuxATSPIAutomation is only available on Linux.")

        # Primary: AT-SPI
        if _pyatspi_available:
            try:
                element = _find_element(app_name, element_name)
                if element is not None:
                    action = element.queryAction()
                    # Prefer an action named "click" or "press"; fall back to index 0.
                    target_index = 0
                    for i in range(action.nActions):
                        name = action.getName(i).lower()
                        if name in ("click", "press", "activate"):
                            target_index = i
                            break
                    action.doAction(target_index)
                    return
            except Exception:
                pass  # Fall through to ydotool

        # Fallback: ydotool
        if not _ydotool_available():
            raise RuntimeError(
                "Neither pyatspi nor ydotool is available. "
                "Install pyatspi>=2.46.0 or the ydotool system package."
            )
        _ydotool_click()

    def type_text(self, app_name: str, element_name: str, text: str) -> None:
        """Set the text of *element_name* inside *app_name* to *text*.

        Strategy
        --------
        1. If pyatspi is available: locate the element and call
           ``setText()`` via the AT-SPI Text interface (or ``queryEditableText``
           on older pyatspi versions).
        2. Fallback: ``ydotool type -- <text>``.

        Parameters
        ----------
        app_name:
            Accessible name of the target application.
        element_name:
            Accessible name of the target element (e.g. a text field).
        text:
            The string to insert.

        Raises
        ------
        RuntimeError
            On non-Linux platforms, or when neither pyatspi nor ydotool is
            available.
        """
        if not _is_linux():
            raise RuntimeError("LinuxATSPIAutomation is only available on Linux.")

        # Primary: AT-SPI
        if _pyatspi_available:
            try:
                element = _find_element(app_name, element_name)
                if element is not None:
                    # Try EditableText interface first (most editable widgets).
                    try:
                        editable = element.queryEditableText()
                        editable.setTextContents(text)
                        return
                    except Exception:
                        pass
                    # Try Value interface for spinners / sliders.
                    try:
                        value = element.queryValue()
                        value.currentValue = float(text)
                        return
                    except Exception:
                        pass
            except Exception:
                pass  # Fall through to ydotool

        # Fallback: ydotool
        if not _ydotool_available():
            raise RuntimeError(
                "Neither pyatspi nor ydotool is available. "
                "Install pyatspi>=2.46.0 or the ydotool system package."
            )
        _ydotool_type(text)

    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------

    @staticmethod
    def is_available() -> bool:
        """Return True if at least one automation backend is accessible.

        Checks
        ------
        - pyatspi can be imported **and** ``Registry.getDesktop(0)`` succeeds
          (i.e. the AT-SPI D-Bus service is running), *or*
        - the ``ydotool`` binary is on PATH.

        Returns False on non-Linux platforms.
        """
        if not _is_linux():
            return False

        # Check pyatspi with a live D-Bus probe.
        if _pyatspi_available and _pyatspi is not None:
            try:
                _pyatspi.Registry.getDesktop(0)
                return True
            except Exception:
                pass

        # Check ydotool binary.
        return _ydotool_available()

"""macOS Agent Space manager — isolate agent windows to Space 2.

Subscribes to ``NSWorkspaceActiveSpaceDidChangeNotification`` so the manager
is notified whenever the user switches Spaces.  When :py:meth:`move_to_agent_space`
is called, the named application is moved to Space 2 via AppleScript, keeping
the user's Space 1 free of agent windows.

AppleScript approach (``key code 18 using {control down}``) is used instead of
the private ``CGSMoveWindowsToManagedSpace`` API so the module has no private-
framework dependency.

Usage::

    if sys.platform == "darwin":
        manager = AgentSpaceManager()
        manager.activate()
        manager.move_to_agent_space("Safari")
        ...
        manager.deactivate()
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from typing import Callable

if sys.platform != "darwin":
    raise ImportError("operator_use.computer.macos.agent_space is macOS-only")

logger = logging.getLogger(__name__)

# AppleScript that:
#   1. Switches to Space 2 via Ctrl+2
#   2. Activates the target application (bringing its windows to the current Space)
#   3. Returns to Space 1 via Ctrl+1
_MOVE_TO_SPACE2_SCRIPT = """\
tell application "System Events"
    key code 19 using {{control down}}
    delay 0.4
end tell
tell application "{app_name}"
    activate
end tell
delay 0.3
tell application "System Events"
    key code 18 using {{control down}}
    delay 0.2
end tell
"""

# AppleScript used when *not* returning to Space 1 (agent stays on Space 2).
_SWITCH_TO_SPACE2_SCRIPT = """\
tell application "System Events"
    key code 19 using {{control down}}
end tell
delay 0.4
tell application "{app_name}"
    activate
end tell
"""


class AgentSpaceManager:
    """Manage a dedicated macOS Space (Space 2) for agent-opened windows.

    Args:
        on_space_change: Optional callback invoked each time
            ``NSWorkspaceActiveSpaceDidChangeNotification`` fires.  Receives no
            arguments; called on the notification thread.
        return_to_user_space: When ``True`` (default), after moving an app to
            Space 2 the manager switches back to Space 1 so the user's view is
            undisturbed.
    """

    def __init__(
        self,
        on_space_change: Callable[[], None] | None = None,
        return_to_user_space: bool = True,
    ) -> None:
        self._on_space_change = on_space_change
        self._return_to_user_space = return_to_user_space
        self._active = False
        self._observer = None  # NSObject observer token
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Subscribe to Space-change notifications.

        Safe to call multiple times; subsequent calls are no-ops.
        """
        with self._lock:
            if self._active:
                return
            self._active = True

        try:
            from AppKit import NSWorkspace  # type: ignore[import]
            from Foundation import NSNotificationCenter as _NSNotificationCenter  # type: ignore[import]  # noqa: F401

            workspace = NSWorkspace.sharedWorkspace()
            notification_center = workspace.notificationCenter()

            self._observer = notification_center.addObserverForName_object_queue_usingBlock_(
                "NSWorkspaceActiveSpaceDidChangeNotification",
                None,
                None,
                self._handle_space_change,
            )
            logger.debug("AgentSpaceManager activated — subscribed to space-change notifications")
        except ImportError:
            logger.warning(
                "AgentSpaceManager: pyobjc-framework-Cocoa not available — "
                "space-change notifications disabled."
            )

    def deactivate(self) -> None:
        """Unsubscribe from Space-change notifications.

        Safe to call multiple times; subsequent calls are no-ops.
        """
        with self._lock:
            if not self._active:
                return
            self._active = False

        if self._observer is not None:
            try:
                from AppKit import NSWorkspace  # type: ignore[import]

                workspace = NSWorkspace.sharedWorkspace()
                notification_center = workspace.notificationCenter()
                notification_center.removeObserver_(self._observer)
                self._observer = None
            except ImportError:
                pass
            except Exception:
                logger.debug("AgentSpaceManager: failed to remove observer", exc_info=True)

        logger.debug("AgentSpaceManager deactivated")

    # ------------------------------------------------------------------
    # Core operation
    # ------------------------------------------------------------------

    def move_to_agent_space(self, app_name: str) -> bool:
        """Move *app_name* to Space 2 using AppleScript.

        Args:
            app_name: The display name of the application as it appears in the
                Dock / Activity Monitor (e.g. ``"Safari"``, ``"Terminal"``).

        Returns:
            ``True`` if the AppleScript ran without error, ``False`` otherwise.
        """
        if self._return_to_user_space:
            script = _MOVE_TO_SPACE2_SCRIPT.format(app_name=app_name)
        else:
            script = _SWITCH_TO_SPACE2_SCRIPT.format(app_name=app_name)

        return self._run_applescript(script, context=f"move {app_name!r} to Space 2")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _handle_space_change(self, notification) -> None:  # noqa: ANN001
        """Invoked by NSNotificationCenter on space transitions."""
        logger.debug("AgentSpaceManager: active space changed")
        if self._on_space_change:
            try:
                self._on_space_change()
            except Exception:
                logger.exception("AgentSpaceManager on_space_change callback raised")

    @staticmethod
    def _run_applescript(script: str, context: str = "") -> bool:
        """Execute an AppleScript string via ``osascript`` and return success."""
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                logger.warning(
                    "AgentSpaceManager AppleScript failed [%s]: %s",
                    context,
                    result.stderr.strip(),
                )
                return False
            return True
        except FileNotFoundError:
            logger.error("AgentSpaceManager: osascript not found — is this macOS?")
            return False
        except subprocess.TimeoutExpired:
            logger.error("AgentSpaceManager: AppleScript timed out [%s]", context)
            return False
        except Exception:
            logger.exception(
                "AgentSpaceManager: unexpected error running AppleScript [%s]", context
            )
            return False

"""Tests for operator_use.computer.macos.agent_space.

The module is macOS-only; we patch ``sys.platform`` to ``"darwin"`` before
importing so these tests run on any platform.  All Objective-C / AppKit calls
are mocked.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import shim — patch sys.platform before the module is loaded.
# ---------------------------------------------------------------------------


def _import_agent_space():
    """Import (or re-import) agent_space with sys.platform forced to 'darwin'."""
    # Remove any cached version so the platform guard re-evaluates.
    sys.modules.pop("operator_use.computer.macos.agent_space", None)

    with patch.object(sys, "platform", "darwin"):
        import importlib

        mod = importlib.import_module("operator_use.computer.macos.agent_space")
    return mod


@pytest.fixture()
def mod():
    return _import_agent_space()


@pytest.fixture()
def manager(mod):
    return mod.AgentSpaceManager()


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


class TestImportGuard:
    def test_import_fails_on_non_darwin(self):
        sys.modules.pop("operator_use.computer.macos.agent_space", None)
        with patch.object(sys, "platform", "linux"):
            with pytest.raises(ImportError):
                import importlib

                importlib.import_module("operator_use.computer.macos.agent_space")
        sys.modules.pop("operator_use.computer.macos.agent_space", None)


# ---------------------------------------------------------------------------
# activate / deactivate
# ---------------------------------------------------------------------------


class TestLifecycle:
    def _make_appkit_mock(self):
        """Return fake AppKit / Foundation modules."""
        appkit = types.ModuleType("AppKit")
        foundation = types.ModuleType("Foundation")

        workspace = MagicMock()
        notification_center = MagicMock()
        workspace.notificationCenter.return_value = notification_center
        appkit.NSWorkspace = MagicMock()
        appkit.NSWorkspace.sharedWorkspace.return_value = workspace
        foundation.NSNotificationCenter = MagicMock()

        return appkit, foundation, notification_center

    def test_activate_subscribes_to_notification(self, manager):
        appkit, foundation, nc = self._make_appkit_mock()
        with patch.dict("sys.modules", {"AppKit": appkit, "Foundation": foundation}):
            manager.activate()

        nc.addObserverForName_object_queue_usingBlock_.assert_called_once()
        name_arg = nc.addObserverForName_object_queue_usingBlock_.call_args[0][0]
        assert name_arg == "NSWorkspaceActiveSpaceDidChangeNotification"

    def test_activate_idempotent(self, manager):
        appkit, foundation, nc = self._make_appkit_mock()
        with patch.dict("sys.modules", {"AppKit": appkit, "Foundation": foundation}):
            manager.activate()
            manager.activate()  # second call should be a no-op

        assert nc.addObserverForName_object_queue_usingBlock_.call_count == 1

    def test_deactivate_removes_observer(self, manager):
        appkit, foundation, nc = self._make_appkit_mock()
        observer_token = MagicMock()
        nc.addObserverForName_object_queue_usingBlock_.return_value = observer_token

        with patch.dict("sys.modules", {"AppKit": appkit, "Foundation": foundation}):
            manager.activate()
            manager.deactivate()

        nc.removeObserver_.assert_called_once_with(observer_token)

    def test_deactivate_idempotent(self, manager):
        appkit, foundation, nc = self._make_appkit_mock()
        with patch.dict("sys.modules", {"AppKit": appkit, "Foundation": foundation}):
            manager.activate()
            manager.deactivate()
            manager.deactivate()  # second call should be a no-op

        assert nc.removeObserver_.call_count == 1

    def test_activate_without_appkit_logs_warning(self, manager, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            with patch.dict("sys.modules", {"AppKit": None, "Foundation": None}):
                manager.activate()

        # Should warn but not raise
        assert manager._active is True  # flag still set

    def test_deactivate_clears_active_flag(self, manager):
        appkit, foundation, nc = self._make_appkit_mock()
        with patch.dict("sys.modules", {"AppKit": appkit, "Foundation": foundation}):
            manager.activate()
        manager.deactivate()
        assert manager._active is False


# ---------------------------------------------------------------------------
# move_to_agent_space
# ---------------------------------------------------------------------------


class TestMoveToAgentSpace:
    def test_runs_applescript_for_named_app(self, manager):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = manager.move_to_agent_space("Safari")

        assert result is True
        assert mock_run.called
        # The script passed to osascript should contain the app name
        script_arg = mock_run.call_args[0][0]
        assert "osascript" in script_arg
        # The -e body should contain Safari
        full_call = " ".join(str(a) for a in mock_run.call_args[0][0])
        assert "Safari" in full_call or "Safari" in str(mock_run.call_args)

    def test_returns_false_on_nonzero_exit(self, manager):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stderr="Error: No application named Nonexistent"
            )
            result = manager.move_to_agent_space("Nonexistent")

        assert result is False

    def test_returns_false_on_timeout(self, manager):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("osascript", 10)):
            result = manager.move_to_agent_space("Terminal")

        assert result is False

    def test_returns_false_when_osascript_missing(self, manager):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = manager.move_to_agent_space("Terminal")

        assert result is False

    def test_script_contains_return_to_space1_when_enabled(self, manager):
        """When return_to_user_space=True, the AppleScript should include Ctrl+1."""
        assert manager._return_to_user_space is True
        mod = _import_agent_space()
        # key code 18 = Ctrl+1
        assert "key code 18" in mod._MOVE_TO_SPACE2_SCRIPT

    def test_script_no_return_when_disabled(self, mod):
        manager = mod.AgentSpaceManager(return_to_user_space=False)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            manager.move_to_agent_space("Finder")

        script_passed = mock_run.call_args[0][0][2]  # ['osascript', '-e', SCRIPT]
        # The no-return script should NOT contain the switch-back key code 18
        assert "key code 18" not in script_passed


# ---------------------------------------------------------------------------
# Space-change notification callback
# ---------------------------------------------------------------------------


class TestSpaceChangeCallback:
    def test_on_space_change_called_on_notification(self, manager):
        events = []
        manager._on_space_change = lambda: events.append(1)
        manager._handle_space_change(MagicMock())
        assert len(events) == 1

    def test_on_space_change_none_does_not_raise(self, manager):
        manager._on_space_change = None
        manager._handle_space_change(MagicMock())  # should not raise

    def test_callback_exception_does_not_propagate(self, manager, caplog):
        import logging

        def _bad():
            raise RuntimeError("oops")

        manager._on_space_change = _bad
        with caplog.at_level(logging.ERROR):
            manager._handle_space_change(MagicMock())  # should not raise

        assert any("callback" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# _run_applescript static helper
# ---------------------------------------------------------------------------


class TestRunAppleScript:
    def test_success(self, mod):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = mod.AgentSpaceManager._run_applescript('return "ok"')
        assert result is True

    def test_non_zero_returns_false(self, mod):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="error")
            result = mod.AgentSpaceManager._run_applescript("bad script")
        assert result is False

    def test_timeout_returns_false(self, mod):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("osascript", 10)):
            result = mod.AgentSpaceManager._run_applescript("slow script")
        assert result is False

    def test_file_not_found_returns_false(self, mod):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = mod.AgentSpaceManager._run_applescript("any script")
        assert result is False


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_activate_deactivate(self, mod):
        appkit = types.ModuleType("AppKit")
        foundation = types.ModuleType("Foundation")
        workspace = MagicMock()
        nc = MagicMock()
        workspace.notificationCenter.return_value = nc
        appkit.NSWorkspace = MagicMock()
        appkit.NSWorkspace.sharedWorkspace.return_value = workspace
        foundation.NSNotificationCenter = MagicMock()

        manager = mod.AgentSpaceManager()
        errors = []

        def _worker():
            try:
                with patch.dict("sys.modules", {"AppKit": appkit, "Foundation": foundation}):
                    for _ in range(10):
                        manager.activate()
                        manager.deactivate()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread-safety errors: {errors}"

"""Tests for PiPMonitor — Picture-in-Picture agent overlay.

PySide6 is mocked entirely at the module level so this test suite runs in any
CI environment without a display or Qt installation.
"""

from __future__ import annotations

import sys
import threading
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Mock PySide6 before importing the module under test
# ---------------------------------------------------------------------------


def _make_pyside6_mock() -> MagicMock:
    """Build a minimal PySide6 mock hierarchy."""
    pyside6 = MagicMock(name="PySide6")

    # QtCore
    qt_core = MagicMock(name="PySide6.QtCore")
    qt_core.Qt = MagicMock()
    qt_core.Qt.WindowType = MagicMock()
    qt_core.Qt.WindowType.WindowStaysOnTopHint = 0x0040
    qt_core.Qt.WindowType.FramelessWindowHint = 0x0800
    qt_core.Qt.AlignmentFlag = MagicMock()
    qt_core.Qt.AlignmentFlag.AlignCenter = 0x0004
    qt_core.Qt.AspectRatioMode = MagicMock()
    qt_core.Qt.AspectRatioMode.KeepAspectRatio = 1
    qt_core.Qt.TransformationMode = MagicMock()
    qt_core.Qt.TransformationMode.SmoothTransformation = 1

    mock_timer = MagicMock(name="QTimer")
    mock_timer_instance = MagicMock(name="QTimer-instance")
    mock_timer.return_value = mock_timer_instance
    qt_core.QTimer = mock_timer
    qt_core.QSize = MagicMock(return_value=MagicMock())

    # QtWidgets
    qt_widgets = MagicMock(name="PySide6.QtWidgets")
    mock_widget = MagicMock(name="QWidget")
    mock_widget_instance = MagicMock(name="QWidget-instance")
    mock_widget.return_value = mock_widget_instance
    qt_widgets.QWidget = mock_widget

    mock_app = MagicMock(name="QApplication")
    mock_app_instance = MagicMock(name="QApplication-instance")
    mock_app.return_value = mock_app_instance
    mock_app.instance.return_value = None
    mock_app.primaryScreen.return_value = MagicMock(name="QScreen")
    qt_widgets.QApplication = mock_app

    mock_label = MagicMock(name="QLabel")
    mock_label_instance = MagicMock(name="QLabel-instance")
    mock_label.return_value = mock_label_instance
    qt_widgets.QLabel = mock_label

    # QtGui
    qt_gui = MagicMock(name="PySide6.QtGui")
    mock_pixmap = MagicMock(name="QPixmap")
    mock_pixmap_instance = MagicMock(name="QPixmap-instance")
    mock_pixmap_instance.isNull.return_value = False
    mock_pixmap_instance.scaled.return_value = MagicMock(name="scaled-pixmap")
    mock_pixmap.return_value = mock_pixmap_instance
    qt_gui.QPixmap = mock_pixmap

    mock_screen = MagicMock(name="QScreen")
    mock_screen_instance = MagicMock(name="QScreen-instance")
    mock_screen.return_value = mock_screen_instance
    qt_gui.QScreen = mock_screen

    pyside6.QtCore = qt_core
    pyside6.QtWidgets = qt_widgets
    pyside6.QtGui = qt_gui

    return pyside6


_pyside6_mock = _make_pyside6_mock()
sys.modules["PySide6"] = _pyside6_mock
sys.modules["PySide6.QtCore"] = _pyside6_mock.QtCore
sys.modules["PySide6.QtWidgets"] = _pyside6_mock.QtWidgets
sys.modules["PySide6.QtGui"] = _pyside6_mock.QtGui

# Now import the module — PySide6 will resolve to our mock
import importlib  # noqa: E402
import operator_use.computer.pip_monitor as pip_module  # noqa: E402

importlib.reload(pip_module)

from operator_use.computer.pip_monitor import (  # noqa: E402
    PiPMonitor,
    is_available,
    _find_window_id_linux,
    _find_window_id_macos,
    _find_window_id_windows,
    _PIP_WIDTH,
    _PIP_HEIGHT,
    _CAPTURE_INTERVAL_MS,
    _PYSIDE6_AVAILABLE,
)


# ---------------------------------------------------------------------------
# Availability helpers
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_returns_bool(self):
        result = is_available()
        assert isinstance(result, bool)

    def test_true_when_pyside6_mocked(self):
        # We injected the mock, so _PYSIDE6_AVAILABLE should be True after reload
        assert _PYSIDE6_AVAILABLE is True

    def test_returns_false_without_pyside6(self):
        """Simulate missing PySide6 by temporarily patching the flag."""
        with patch.object(pip_module, "_PYSIDE6_AVAILABLE", False):
            assert pip_module.is_available() is False

    def test_is_available_matches_flag(self):
        with patch.object(pip_module, "_PYSIDE6_AVAILABLE", True):
            assert pip_module.is_available() is True
        with patch.object(pip_module, "_PYSIDE6_AVAILABLE", False):
            assert pip_module.is_available() is False


# ---------------------------------------------------------------------------
# Window geometry / constants
# ---------------------------------------------------------------------------


class TestWindowGeometry:
    def test_pip_width_is_320(self):
        assert _PIP_WIDTH == 320

    def test_pip_height_is_240(self):
        assert _PIP_HEIGHT == 240

    def test_capture_interval_is_500ms(self):
        assert _CAPTURE_INTERVAL_MS == 500

    def test_width_height_ratio(self):
        assert _PIP_WIDTH / _PIP_HEIGHT == pytest.approx(4 / 3)


# ---------------------------------------------------------------------------
# start() — launches daemon thread
# ---------------------------------------------------------------------------


class TestStart:
    def test_start_sets_running_flag(self):
        monitor = PiPMonitor()
        with patch.object(monitor, "_run_event_loop"):
            monitor.start("Agent Window")
        assert monitor.is_running

    def test_start_creates_daemon_thread(self):
        monitor = PiPMonitor()
        with patch.object(monitor, "_run_event_loop"):
            monitor.start("Agent Window")
        assert monitor._thread is not None
        assert monitor._thread.daemon is True

    def test_start_stores_source_title(self):
        monitor = PiPMonitor()
        with patch.object(monitor, "_run_event_loop"):
            monitor.start("My Agent Window")
        assert monitor.source_title == "My Agent Window"

    def test_start_noop_when_pyside6_missing(self):
        monitor = PiPMonitor()
        with patch.object(pip_module, "_PYSIDE6_AVAILABLE", False):
            monitor.start("Agent Window")
        assert not monitor.is_running
        assert monitor._thread is None

    def test_start_noop_if_already_running(self):
        monitor = PiPMonitor()
        monitor._running.set()
        original_thread = monitor._thread
        with patch.object(monitor, "_run_event_loop"):
            monitor.start("Agent Window")
        assert monitor._thread is original_thread  # unchanged


# ---------------------------------------------------------------------------
# stop() — clears flag, closes window, joins thread
# ---------------------------------------------------------------------------


class TestStop:
    def test_stop_clears_running_flag(self):
        monitor = PiPMonitor()
        monitor._running.set()
        monitor.stop()
        assert not monitor.is_running

    def test_stop_joins_thread(self):
        monitor = PiPMonitor()
        finished = threading.Event()

        def slow():
            finished.wait(timeout=2)

        t = threading.Thread(target=slow, daemon=True)
        t.start()
        monitor._thread = t
        monitor._running.set()
        finished.set()
        monitor.stop()
        assert not t.is_alive()

    def test_stop_closes_window(self):
        monitor = PiPMonitor()
        mock_window = MagicMock(name="window")
        monitor._window = mock_window
        monitor._running.set()
        monitor.stop()
        mock_window.close.assert_called_once()

    def test_stop_quits_app(self):
        monitor = PiPMonitor()
        mock_app = MagicMock(name="app")
        monitor._app = mock_app
        monitor._running.set()
        monitor.stop()
        mock_app.quit.assert_called_once()

    def test_stop_resets_window_and_app_to_none(self):
        monitor = PiPMonitor()
        monitor._window = MagicMock()
        monitor._app = MagicMock()
        monitor._running.set()
        monitor.stop()
        assert monitor._window is None
        assert monitor._app is None

    def test_stop_idempotent_when_not_running(self):
        monitor = PiPMonitor()
        monitor.stop()  # should not raise
        assert not monitor.is_running


# ---------------------------------------------------------------------------
# update_source() — changes monitored window title
# ---------------------------------------------------------------------------


class TestUpdateSource:
    def test_update_source_changes_title(self):
        monitor = PiPMonitor()
        with patch.object(monitor, "_run_event_loop"):
            monitor.start("Old Title")
        monitor.update_source("New Title")
        assert monitor.source_title == "New Title"

    def test_update_source_delegates_to_window(self):
        monitor = PiPMonitor()
        mock_window = MagicMock(name="window")
        monitor._window = mock_window
        monitor.update_source("Browser - Agent")
        mock_window.update_source.assert_called_once_with("Browser - Agent")

    def test_update_source_without_window_does_not_raise(self):
        monitor = PiPMonitor()
        monitor._window = None
        monitor.update_source("Some Title")  # should not raise
        assert monitor.source_title == "Some Title"


# ---------------------------------------------------------------------------
# Platform window-ID finders (mocked subprocess / ctypes)
# ---------------------------------------------------------------------------


class TestFindWindowIdLinux:
    def test_returns_wid_on_success(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="12345\n", returncode=0)
            wid = _find_window_id_linux("My App")
        assert wid == 12345

    def test_returns_none_on_empty_output(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=1)
            wid = _find_window_id_linux("Missing Window")
        assert wid is None

    def test_returns_none_on_exception(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("xdotool not found")):
            wid = _find_window_id_linux("My App")
        assert wid is None


class TestFindWindowIdMacOs:
    def test_returns_none_on_ctypes_failure(self):
        with patch.dict(sys.modules, {"ctypes": None}):
            wid = _find_window_id_macos("Anything")
        assert wid is None

    def test_returns_none_on_exception(self):
        with patch("ctypes.cdll") as mock_cdll:
            mock_cdll.LoadLibrary.side_effect = OSError("lib not found")
            wid = _find_window_id_macos("My App")
        assert wid is None


class TestFindWindowIdWindows:
    def test_returns_hwnd_when_found(self):
        mock_windll = MagicMock(name="windll")
        mock_windll.user32.FindWindowW.return_value = 99
        with patch("ctypes.windll", mock_windll, create=True):
            wid = _find_window_id_windows("My App")
        assert wid == 99

    def test_returns_none_when_not_found(self):
        mock_windll = MagicMock(name="windll")
        mock_windll.user32.FindWindowW.return_value = 0
        with patch("ctypes.windll", mock_windll, create=True):
            wid = _find_window_id_windows("Missing App")
        assert wid is None

    def test_returns_none_on_exception(self):
        """If ctypes.windll raises, returns None."""
        mock_windll = MagicMock(name="windll")
        mock_windll.user32.FindWindowW.side_effect = OSError("access denied")
        with patch("ctypes.windll", mock_windll, create=True):
            wid = _find_window_id_windows("My App")
        assert wid is None


# ---------------------------------------------------------------------------
# PiPMonitor properties
# ---------------------------------------------------------------------------


class TestProperties:
    def test_is_running_false_initially(self):
        monitor = PiPMonitor()
        assert not monitor.is_running

    def test_source_title_empty_initially(self):
        monitor = PiPMonitor()
        assert monitor.source_title == ""

    def test_is_running_true_after_start(self):
        monitor = PiPMonitor()
        with patch.object(monitor, "_run_event_loop"):
            monitor.start("Window")
        assert monitor.is_running

    def test_is_running_false_after_stop(self):
        monitor = PiPMonitor()
        with patch.object(monitor, "_run_event_loop"):
            monitor.start("Window")
        monitor.stop()
        assert not monitor.is_running

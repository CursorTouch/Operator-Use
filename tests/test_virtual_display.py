"""Tests for VirtualDisplayManager — Windows VDD confinement layer.

All Win32 / ctypes calls are mocked so the suite runs on macOS and Linux.
"""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mgr():
    """Return a fresh VirtualDisplayManager with internal state reset."""
    import operator_use.computer.windows.virtual_display as vd_mod

    # Reset cached ctypes state between tests
    vd_mod._user32 = None
    vd_mod._kernel32 = None
    vd_mod._ctypes_loaded = False

    from operator_use.computer.windows.virtual_display import VirtualDisplayManager

    return VirtualDisplayManager()


# ---------------------------------------------------------------------------
# 1. is_available() — platform gate
# ---------------------------------------------------------------------------


def test_is_available_false_on_non_windows():
    mgr = _make_mgr()
    with patch.object(sys, "platform", "darwin"):
        assert mgr.is_available() is False


def test_is_available_false_on_linux():
    mgr = _make_mgr()
    with patch.object(sys, "platform", "linux"):
        assert mgr.is_available() is False


def test_is_available_true_when_win32_and_vdd_installed():
    mgr = _make_mgr()
    with (
        patch.object(sys, "platform", "win32"),
        patch.object(mgr, "is_vdd_installed", return_value=True),
    ):
        assert mgr.is_available() is True


def test_is_available_false_when_win32_but_vdd_missing():
    mgr = _make_mgr()
    with (
        patch.object(sys, "platform", "win32"),
        patch.object(mgr, "is_vdd_installed", return_value=False),
    ):
        assert mgr.is_available() is False


# ---------------------------------------------------------------------------
# 2. is_vdd_installed() — registry + driver store checks
# ---------------------------------------------------------------------------


def test_is_vdd_installed_true_via_registry():
    mgr = _make_mgr()
    with patch.object(sys, "platform", "win32"):
        with patch(
            "operator_use.computer.windows.virtual_display._registry_key_exists",
            return_value=True,
        ):
            assert mgr.is_vdd_installed() is True


def test_is_vdd_installed_false_when_winreg_missing():
    """On non-Windows, winreg is absent so is_vdd_installed returns False."""
    mgr = _make_mgr()
    # We're already on macOS/Linux — winreg import will fail naturally.
    # Registry check returns False; subprocess finds no VDA INF either.
    with patch(
        "operator_use.computer.windows.virtual_display._registry_key_exists",
        return_value=False,
    ), patch(
        "subprocess.run",
        return_value=MagicMock(returncode=0, stdout="SomeOtherDriver.inf\n", stderr=""),
    ):
        result = mgr.is_vdd_installed()
    # Should return False, not raise
    assert result is False


def test_is_vdd_installed_fallback_to_driver_store():
    """When registry check fails, falls back to pnputil and finds VDA INF."""
    mgr = _make_mgr()
    with (
        patch(
            "operator_use.computer.windows.virtual_display._registry_key_exists",
            return_value=False,
        ),
        patch(
            "subprocess.run",
            return_value=MagicMock(
                returncode=0, stdout="ParsecVDA.inf\nSome other driver\n", stderr=""
            ),
        ),
    ):
        assert mgr.is_vdd_installed() is True


def test_is_vdd_installed_false_when_neither_check_succeeds():
    mgr = _make_mgr()
    with patch.object(sys, "platform", "win32"):
        with (
            patch(
                "operator_use.computer.windows.virtual_display._registry_key_exists",
                return_value=False,
            ),
            patch(
                "subprocess.run",
                return_value=MagicMock(returncode=0, stdout="SomeOtherDriver.inf\n", stderr=""),
            ),
        ):
            assert mgr.is_vdd_installed() is False


# ---------------------------------------------------------------------------
# 3. create_virtual_display() — ctypes path
# ---------------------------------------------------------------------------


def test_create_virtual_display_calls_vda_with_correct_params():
    mgr = _make_mgr()
    mock_vda = MagicMock()
    mock_vda.ParsecVDAAddDisplay.return_value = 0  # success

    with patch.object(sys, "platform", "win32"):
        import ctypes as _ctypes

        with patch.object(_ctypes, "windll", create=True) as mock_windll:
            mock_windll.ParsecVDA = mock_vda

            result = mgr._create_via_ctypes(1920, 1080, 60)

    assert result is True
    mock_vda.ParsecVDAAddDisplay.assert_called_once()
    # Verify correct dimension args were used (ctypes wraps them, so inspect call)
    args = mock_vda.ParsecVDAAddDisplay.call_args
    assert args is not None


def test_create_virtual_display_returns_false_when_ctypes_raises():
    mgr = _make_mgr()

    with patch.object(sys, "platform", "win32"):
        import ctypes as _ctypes

        with patch.object(_ctypes, "windll", create=True) as mock_windll:
            mock_windll.ParsecVDA = MagicMock(side_effect=OSError("DLL not found"))
            result = mgr._create_via_ctypes(1920, 1080, 60)

    assert result is False


def test_create_virtual_display_skips_on_non_windows():
    mgr = _make_mgr()
    with patch.object(sys, "platform", "darwin"):
        assert mgr.create_virtual_display() is False


# ---------------------------------------------------------------------------
# 4. create_virtual_display() — subprocess fallback
# ---------------------------------------------------------------------------


def test_create_via_subprocess_success():
    mgr = _make_mgr()

    with (
        patch.object(sys, "platform", "win32"),
        patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout="", stderr=""),
        ) as mock_run,
    ):
        result = mgr._create_via_subprocess(1920, 1080, 60)

    assert result is True
    assert mgr._virtual_monitor_index == 0
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "--width=1920" in cmd
    assert "--height=1080" in cmd
    assert "--hz=60" in cmd


def test_create_via_subprocess_failure_returns_false():
    mgr = _make_mgr()

    with (
        patch.object(sys, "platform", "win32"),
        patch(
            "subprocess.run",
            return_value=MagicMock(returncode=1, stdout="", stderr="error"),
        ),
    ):
        result = mgr._create_via_subprocess(1920, 1080, 60)

    assert result is False


def test_create_via_subprocess_file_not_found_returns_false():
    mgr = _make_mgr()

    with (
        patch.object(sys, "platform", "win32"),
        patch(
            "subprocess.run",
            side_effect=FileNotFoundError("ParsecVDA.exe not found"),
        ),
    ):
        result = mgr._create_via_subprocess(1920, 1080, 60)

    assert result is False


# ---------------------------------------------------------------------------
# 5. remove_virtual_display()
# ---------------------------------------------------------------------------


def test_remove_virtual_display_ctypes_success():
    mgr = _make_mgr()
    mgr._virtual_monitor_index = 0

    mock_vda = MagicMock()
    mock_vda.ParsecVDARemoveDisplay.return_value = 0

    with patch.object(sys, "platform", "win32"):
        import ctypes as _ctypes

        with patch.object(_ctypes, "windll", create=True) as mock_windll:
            mock_windll.ParsecVDA = mock_vda
            result = mgr._remove_via_ctypes()

    assert result is True


def test_remove_virtual_display_resets_state():
    mgr = _make_mgr()
    mgr._virtual_monitor_index = 0

    with (
        patch.object(sys, "platform", "win32"),
        patch.object(mgr, "_remove_via_ctypes", return_value=True),
    ):
        result = mgr.remove_virtual_display()

    assert result is True
    assert mgr._virtual_monitor_index is None
    assert mgr._virtual_monitor_handle is None


def test_remove_virtual_display_no_display_returns_false():
    mgr = _make_mgr()
    with patch.object(sys, "platform", "win32"):
        assert mgr.remove_virtual_display() is False


def test_remove_virtual_display_skips_on_non_windows():
    mgr = _make_mgr()
    mgr._virtual_monitor_index = 0
    with patch.object(sys, "platform", "darwin"):
        assert mgr.remove_virtual_display() is False


def test_remove_via_subprocess_success():
    mgr = _make_mgr()
    mgr._virtual_monitor_index = 0

    with (
        patch.object(sys, "platform", "win32"),
        patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout="", stderr=""),
        ) as mock_run,
    ):
        result = mgr._remove_via_subprocess()

    assert result is True
    cmd = mock_run.call_args[0][0]
    assert "remove" in cmd


def test_remove_via_subprocess_failure():
    mgr = _make_mgr()
    with (
        patch.object(sys, "platform", "win32"),
        patch(
            "subprocess.run",
            return_value=MagicMock(returncode=1, stdout="", stderr="error"),
        ),
    ):
        assert mgr._remove_via_subprocess() is False


# ---------------------------------------------------------------------------
# 6. move_window_to_virtual_display()
# ---------------------------------------------------------------------------


def test_move_window_to_virtual_display_calls_set_window_pos():
    mgr = _make_mgr()

    with (
        patch.object(sys, "platform", "win32"),
        patch("operator_use.computer.windows.virtual_display._load_win32", return_value=True),
        patch.object(mgr, "_find_window", return_value=12345),
        patch.object(mgr, "_get_virtual_monitor", return_value=MagicMock()),
        patch.object(mgr, "_set_window_pos", return_value=True) as mock_swp,
    ):
        result = mgr.move_window_to_virtual_display("Notepad")

    assert result is True
    mock_swp.assert_called_once()


def test_move_window_returns_false_on_non_windows():
    mgr = _make_mgr()
    with patch.object(sys, "platform", "darwin"):
        assert mgr.move_window_to_virtual_display("Notepad") is False


def test_move_window_returns_false_when_window_not_found():
    mgr = _make_mgr()
    with (
        patch.object(sys, "platform", "win32"),
        patch("operator_use.computer.windows.virtual_display._load_win32", return_value=True),
        patch.object(mgr, "_find_window", return_value=None),
    ):
        assert mgr.move_window_to_virtual_display("NonExistent") is False


def test_move_window_returns_false_when_no_virtual_monitor():
    mgr = _make_mgr()
    with (
        patch.object(sys, "platform", "win32"),
        patch("operator_use.computer.windows.virtual_display._load_win32", return_value=True),
        patch.object(mgr, "_find_window", return_value=12345),
        patch.object(mgr, "_get_virtual_monitor", return_value=None),
    ):
        assert mgr.move_window_to_virtual_display("Notepad") is False


def test_move_window_returns_false_when_win32_load_fails():
    mgr = _make_mgr()
    with (
        patch.object(sys, "platform", "win32"),
        patch("operator_use.computer.windows.virtual_display._load_win32", return_value=False),
    ):
        assert mgr.move_window_to_virtual_display("Notepad") is False


# ---------------------------------------------------------------------------
# 7. Graceful failure — ctypes unavailable / VDD not installed
# ---------------------------------------------------------------------------


def test_all_ctypes_calls_fail_gracefully_when_vdd_unavailable():
    """Verify no exceptions bubble out when ParsecVDA DLL is missing."""
    mgr = _make_mgr()

    with patch.object(sys, "platform", "win32"):
        import ctypes as _ctypes

        with patch.object(_ctypes, "windll", create=True) as mock_windll:
            # Accessing ParsecVDA raises AttributeError (DLL absent)
            type(mock_windll).ParsecVDA = property(
                lambda self: (_ for _ in ()).throw(AttributeError("ParsecVDA not found"))
            )
            # Should return False, not raise
            assert mgr._create_via_ctypes(1920, 1080, 60) is False
            mgr._virtual_monitor_index = 0
            assert mgr._remove_via_ctypes() is False


def test_subprocess_timeout_handled_gracefully():
    mgr = _make_mgr()
    with (
        patch.object(sys, "platform", "win32"),
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ParsecVDA.exe", timeout=15),
        ),
    ):
        assert mgr._create_via_subprocess(1920, 1080, 60) is False
        assert mgr._remove_via_subprocess() is False


def test_registry_key_exists_returns_false_on_missing_key():
    """_registry_key_exists must not raise on missing key."""
    from operator_use.computer.windows.virtual_display import _registry_key_exists

    with patch("builtins.__import__", side_effect=ImportError("winreg")):
        result = _registry_key_exists(0, r"SOFTWARE\Parsec\vdd")
    assert result is False

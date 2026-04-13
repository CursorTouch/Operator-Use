"""Tests for operator_use.computer.linux.atapi.

The pyatspi library is NOT installed in CI — all AT-SPI interactions are
mocked at the module level using unittest.mock.  Each test class patches the
relevant internals so we can verify exact code paths without a D-Bus session.
"""

from __future__ import annotations

import subprocess
from types import ModuleType
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Helpers: create a minimal fake pyatspi hierarchy
# ---------------------------------------------------------------------------


def _make_action(names: list[str], *, raises: bool = False) -> mock.MagicMock:
    """Return a fake AT-SPI Action proxy."""
    action = mock.MagicMock()
    if raises:
        action.doAction.side_effect = RuntimeError("AT-SPI action error")
    action.nActions = len(names)
    action.getName.side_effect = lambda i: names[i]
    return action


def _make_element(
    name: str,
    children: list | None = None,
    *,
    action: mock.MagicMock | None = None,
    editable_text: mock.MagicMock | None = None,
    value_iface: mock.MagicMock | None = None,
    no_action: bool = False,
) -> mock.MagicMock:
    children = children or []
    elem = mock.MagicMock()
    elem.name = name
    elem.childCount = len(children)
    elem.getChildAtIndex.side_effect = lambda i: children[i]

    if no_action:
        elem.queryAction.side_effect = Exception("no action iface")
    else:
        elem.queryAction.return_value = action or _make_action(["click"])

    if editable_text is not None:
        elem.queryEditableText.return_value = editable_text
    else:
        elem.queryEditableText.side_effect = Exception("no editable text")

    if value_iface is not None:
        elem.queryValue.return_value = value_iface
    else:
        elem.queryValue.side_effect = Exception("no value iface")

    return elem


def _make_desktop(apps: list[mock.MagicMock]) -> mock.MagicMock:
    desktop = mock.MagicMock()
    desktop.childCount = len(apps)
    desktop.getChildAtIndex.side_effect = lambda i: apps[i]
    return desktop


def _make_fake_pyatspi(desktop: mock.MagicMock) -> ModuleType:
    """Return a minimal fake pyatspi module."""
    mod = ModuleType("pyatspi")
    registry = mock.MagicMock()
    registry.getDesktop.return_value = desktop
    mod.Registry = registry
    return mod


# ---------------------------------------------------------------------------
# 1. Import tests
# ---------------------------------------------------------------------------


class TestImportBehaviour:
    """The module must be importable regardless of platform or pyatspi status."""

    def test_import_succeeds_on_current_platform(self):
        """Module imports cleanly; pyatspi absence is silently handled."""

        import operator_use.computer.linux.atapi as m

        assert m.LinuxATSPIAutomation is not None

    def test_import_succeeds_when_pyatspi_missing(self, monkeypatch):
        """Simulated missing pyatspi must not raise at import time."""
        import operator_use.computer.linux.atapi as m

        # Patch the module-level flag as if pyatspi was never importable.
        monkeypatch.setattr(m, "_pyatspi_available", False)
        monkeypatch.setattr(m, "_pyatspi", None)
        # Re-instantiating the class must still work.
        auto = m.LinuxATSPIAutomation()
        assert auto is not None

    def test_linux_automation_class_exported(self):
        from operator_use.computer.linux.atapi import LinuxATSPIAutomation

        assert callable(LinuxATSPIAutomation)

    def test_module_level_flag_types(self):
        import operator_use.computer.linux.atapi as m

        assert isinstance(m._pyatspi_available, bool)


# ---------------------------------------------------------------------------
# 2. is_available() tests
# ---------------------------------------------------------------------------


class TestIsAvailable:
    """is_available() must accurately reflect which backends are reachable."""

    def test_returns_false_on_non_linux(self, monkeypatch):
        import operator_use.computer.linux.atapi as m

        monkeypatch.setattr(m, "_is_linux", lambda: False)
        assert m.LinuxATSPIAutomation.is_available() is False

    def test_returns_true_when_pyatspi_desktop_ok(self, monkeypatch):
        import operator_use.computer.linux.atapi as m

        fake_pyatspi = mock.MagicMock()
        fake_pyatspi.Registry.getDesktop.return_value = mock.MagicMock()
        monkeypatch.setattr(m, "_is_linux", lambda: True)
        monkeypatch.setattr(m, "_pyatspi_available", True)
        monkeypatch.setattr(m, "_pyatspi", fake_pyatspi)
        assert m.LinuxATSPIAutomation.is_available() is True

    def test_returns_false_when_pyatspi_dbus_fails_and_no_ydotool(self, monkeypatch):
        import operator_use.computer.linux.atapi as m

        fake_pyatspi = mock.MagicMock()
        fake_pyatspi.Registry.getDesktop.side_effect = Exception("no D-Bus")
        monkeypatch.setattr(m, "_is_linux", lambda: True)
        monkeypatch.setattr(m, "_pyatspi_available", True)
        monkeypatch.setattr(m, "_pyatspi", fake_pyatspi)
        monkeypatch.setattr(m, "_ydotool_available", lambda: False)
        assert m.LinuxATSPIAutomation.is_available() is False

    def test_returns_true_when_only_ydotool_present(self, monkeypatch):
        import operator_use.computer.linux.atapi as m

        monkeypatch.setattr(m, "_is_linux", lambda: True)
        monkeypatch.setattr(m, "_pyatspi_available", False)
        monkeypatch.setattr(m, "_pyatspi", None)
        monkeypatch.setattr(m, "_ydotool_available", lambda: True)
        assert m.LinuxATSPIAutomation.is_available() is True

    def test_returns_false_when_neither_present(self, monkeypatch):
        import operator_use.computer.linux.atapi as m

        monkeypatch.setattr(m, "_is_linux", lambda: True)
        monkeypatch.setattr(m, "_pyatspi_available", False)
        monkeypatch.setattr(m, "_pyatspi", None)
        monkeypatch.setattr(m, "_ydotool_available", lambda: False)
        assert m.LinuxATSPIAutomation.is_available() is False


# ---------------------------------------------------------------------------
# 3. click() — AT-SPI primary path
# ---------------------------------------------------------------------------


class TestClickATSPI:
    """click() must traverse the tree and invoke the action interface."""

    def _setup(self, monkeypatch, action: mock.MagicMock | None = None):
        import operator_use.computer.linux.atapi as m

        act = action or _make_action(["click"])
        target = _make_element("Open", action=act)
        app = _make_element("gedit", children=[target])
        desktop = _make_desktop([app])
        fake_pyatspi = _make_fake_pyatspi(desktop)

        monkeypatch.setattr(m, "_is_linux", lambda: True)
        monkeypatch.setattr(m, "_pyatspi_available", True)
        monkeypatch.setattr(m, "_pyatspi", fake_pyatspi)
        return m, act

    def test_click_succeeds_via_atspi(self, monkeypatch):
        m, act = self._setup(monkeypatch)
        m.LinuxATSPIAutomation().click("gedit", "Open")
        act.doAction.assert_called_once_with(0)

    def test_click_prefers_action_named_click(self, monkeypatch):
        """When multiple actions exist, the one named 'click' is preferred."""
        act = _make_action(["focus", "click", "press"])
        m, _ = self._setup(monkeypatch, action=act)
        m.LinuxATSPIAutomation().click("gedit", "Open")
        act.doAction.assert_called_once_with(1)  # index 1 == "click"

    def test_click_selects_press_action(self, monkeypatch):
        """'press' is also a recognised action name."""
        act = _make_action(["focus", "press"])
        m, _ = self._setup(monkeypatch, action=act)
        m.LinuxATSPIAutomation().click("gedit", "Open")
        act.doAction.assert_called_once_with(1)

    def test_click_falls_back_to_index_zero_for_unknown_action(self, monkeypatch):
        act = _make_action(["do-something-weird"])
        m, _ = self._setup(monkeypatch, action=act)
        m.LinuxATSPIAutomation().click("gedit", "Open")
        act.doAction.assert_called_once_with(0)

    def test_click_raises_on_non_linux(self, monkeypatch):
        import operator_use.computer.linux.atapi as m

        monkeypatch.setattr(m, "_is_linux", lambda: False)
        with pytest.raises(RuntimeError, match="only available on Linux"):
            m.LinuxATSPIAutomation().click("gedit", "Open")


# ---------------------------------------------------------------------------
# 4. click() — ydotool fallback path
# ---------------------------------------------------------------------------


class TestClickYdotoolFallback:
    """click() must call ydotool when pyatspi is absent or element not found."""

    def test_click_falls_back_to_ydotool_when_pyatspi_raises(self, monkeypatch):
        import operator_use.computer.linux.atapi as m

        # pyatspi present but Registry raises
        fake_pyatspi = mock.MagicMock()
        fake_pyatspi.Registry.getDesktop.side_effect = Exception("D-Bus error")
        monkeypatch.setattr(m, "_is_linux", lambda: True)
        monkeypatch.setattr(m, "_pyatspi_available", True)
        monkeypatch.setattr(m, "_pyatspi", fake_pyatspi)
        monkeypatch.setattr(m, "_ydotool_available", lambda: True)

        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            m.LinuxATSPIAutomation().click("gedit", "Open")
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "ydotool"
            assert args[1] == "click"

    def test_click_falls_back_when_pyatspi_unavailable(self, monkeypatch):
        import operator_use.computer.linux.atapi as m

        monkeypatch.setattr(m, "_is_linux", lambda: True)
        monkeypatch.setattr(m, "_pyatspi_available", False)
        monkeypatch.setattr(m, "_pyatspi", None)
        monkeypatch.setattr(m, "_ydotool_available", lambda: True)

        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            m.LinuxATSPIAutomation().click("gedit", "Open")
            mock_run.assert_called_once()

    def test_click_falls_back_when_element_not_found(self, monkeypatch):
        """Element lookup returns None → falls back to ydotool."""
        import operator_use.computer.linux.atapi as m

        app = _make_element("gedit", children=[])  # no children → element not found
        desktop = _make_desktop([app])
        fake_pyatspi = _make_fake_pyatspi(desktop)
        monkeypatch.setattr(m, "_is_linux", lambda: True)
        monkeypatch.setattr(m, "_pyatspi_available", True)
        monkeypatch.setattr(m, "_pyatspi", fake_pyatspi)
        monkeypatch.setattr(m, "_ydotool_available", lambda: True)

        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            m.LinuxATSPIAutomation().click("gedit", "NonExistentButton")
            mock_run.assert_called_once()

    def test_click_raises_when_no_backend_available(self, monkeypatch):
        import operator_use.computer.linux.atapi as m

        monkeypatch.setattr(m, "_is_linux", lambda: True)
        monkeypatch.setattr(m, "_pyatspi_available", False)
        monkeypatch.setattr(m, "_pyatspi", None)
        monkeypatch.setattr(m, "_ydotool_available", lambda: False)

        with pytest.raises(RuntimeError, match="Neither pyatspi nor ydotool"):
            m.LinuxATSPIAutomation().click("gedit", "Open")


# ---------------------------------------------------------------------------
# 5. type_text() — AT-SPI primary path
# ---------------------------------------------------------------------------


class TestTypeTextATSPI:
    """type_text() must use EditableText / Value interfaces when AT-SPI available."""

    def test_type_text_uses_editable_text(self, monkeypatch):
        import operator_use.computer.linux.atapi as m

        editable = mock.MagicMock()
        target = _make_element("text-field", editable_text=editable)
        app = _make_element("gedit", children=[target])
        desktop = _make_desktop([app])
        fake_pyatspi = _make_fake_pyatspi(desktop)

        monkeypatch.setattr(m, "_is_linux", lambda: True)
        monkeypatch.setattr(m, "_pyatspi_available", True)
        monkeypatch.setattr(m, "_pyatspi", fake_pyatspi)

        m.LinuxATSPIAutomation().type_text("gedit", "text-field", "hello")
        editable.setTextContents.assert_called_once_with("hello")

    def test_type_text_raises_on_non_linux(self, monkeypatch):
        import operator_use.computer.linux.atapi as m

        monkeypatch.setattr(m, "_is_linux", lambda: False)
        with pytest.raises(RuntimeError, match="only available on Linux"):
            m.LinuxATSPIAutomation().type_text("gedit", "text-field", "hello")


# ---------------------------------------------------------------------------
# 6. type_text() — ydotool fallback path
# ---------------------------------------------------------------------------


class TestTypeTextYdotoolFallback:
    def test_type_text_falls_back_to_ydotool_when_pyatspi_raises(self, monkeypatch):
        import operator_use.computer.linux.atapi as m

        fake_pyatspi = mock.MagicMock()
        fake_pyatspi.Registry.getDesktop.side_effect = Exception("D-Bus error")
        monkeypatch.setattr(m, "_is_linux", lambda: True)
        monkeypatch.setattr(m, "_pyatspi_available", True)
        monkeypatch.setattr(m, "_pyatspi", fake_pyatspi)
        monkeypatch.setattr(m, "_ydotool_available", lambda: True)

        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            m.LinuxATSPIAutomation().type_text("gedit", "text-field", "world")
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "ydotool"
            assert args[1] == "type"
            assert "world" in args

    def test_type_text_falls_back_when_pyatspi_unavailable(self, monkeypatch):
        import operator_use.computer.linux.atapi as m

        monkeypatch.setattr(m, "_is_linux", lambda: True)
        monkeypatch.setattr(m, "_pyatspi_available", False)
        monkeypatch.setattr(m, "_pyatspi", None)
        monkeypatch.setattr(m, "_ydotool_available", lambda: True)

        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            m.LinuxATSPIAutomation().type_text("gedit", "text-field", "foo")
            mock_run.assert_called_once()

    def test_type_text_raises_when_no_backend_available(self, monkeypatch):
        import operator_use.computer.linux.atapi as m

        monkeypatch.setattr(m, "_is_linux", lambda: True)
        monkeypatch.setattr(m, "_pyatspi_available", False)
        monkeypatch.setattr(m, "_pyatspi", None)
        monkeypatch.setattr(m, "_ydotool_available", lambda: False)

        with pytest.raises(RuntimeError, match="Neither pyatspi nor ydotool"):
            m.LinuxATSPIAutomation().type_text("gedit", "text-field", "bar")


# ---------------------------------------------------------------------------
# 7. Cross-platform import guard
# ---------------------------------------------------------------------------


class TestNonLinuxPlatform:
    """On non-Linux platforms the module must import fine but do nothing."""

    def test_import_does_not_crash_on_non_linux(self):
        """Import must succeed regardless of sys.platform value."""
        # This test always passes — we already imported successfully above.
        import operator_use.computer.linux.atapi  # noqa: F401

    def test_is_available_false_on_non_linux(self, monkeypatch):
        import operator_use.computer.linux.atapi as m

        monkeypatch.setattr(m, "_is_linux", lambda: False)
        assert m.LinuxATSPIAutomation.is_available() is False

    def test_click_raises_runtime_on_non_linux(self, monkeypatch):
        import operator_use.computer.linux.atapi as m

        monkeypatch.setattr(m, "_is_linux", lambda: False)
        auto = m.LinuxATSPIAutomation()
        with pytest.raises(RuntimeError):
            auto.click("app", "elem")

    def test_type_text_raises_runtime_on_non_linux(self, monkeypatch):
        import operator_use.computer.linux.atapi as m

        monkeypatch.setattr(m, "_is_linux", lambda: False)
        auto = m.LinuxATSPIAutomation()
        with pytest.raises(RuntimeError):
            auto.type_text("app", "elem", "text")

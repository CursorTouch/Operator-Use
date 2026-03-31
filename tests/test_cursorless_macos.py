"""Tests for cursorless click path in the macOS computer tool."""

import sys
from unittest.mock import MagicMock

# Stub out macOS-only native modules so tests run on any platform / in CI
for _mod in [
    "Quartz", "Quartz.CoreGraphics",
    "ApplicationServices",
    "CoreFoundation",
    "Cocoa",
    "objc",
]:
    sys.modules.setdefault(_mod, MagicMock())

import asyncio  # noqa: E402

import pytest  # noqa: E402  # ty: ignore[unresolved-import]


@pytest.fixture(autouse=True)
def mock_ax(monkeypatch):
    ax = MagicMock()
    monkeypatch.setattr("operator_use.computer.tools.macos.ax", ax)
    return ax


@pytest.fixture(autouse=True)
def mock_ax_patterns(monkeypatch):
    patterns = MagicMock()
    monkeypatch.setattr("operator_use.computer.tools.macos.ax_patterns", patterns)
    return patterns


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_click_uses_invoke_pattern_when_supported(mock_ax, mock_ax_patterns):
    """Left single click should use InvokePattern and NOT call ax.Click."""
    from operator_use.computer.tools.macos import computer

    element = MagicMock()
    mock_ax.ElementAtPosition.return_value = element
    mock_ax.GetRootControl.return_value = MagicMock()

    invoke = MagicMock()
    invoke.Invoke.return_value = True
    mock_ax_patterns.InvokePattern.IsSupported.return_value = True
    mock_ax_patterns.InvokePattern.return_value = invoke

    result = run(computer.ainvoke(action="click", loc=[100, 200]))

    mock_ax_patterns.InvokePattern.IsSupported.assert_called_once_with(element)
    invoke.Invoke.assert_called_once()
    mock_ax.Click.assert_not_called()
    assert result.success


def test_click_falls_back_when_invoke_not_supported(mock_ax, mock_ax_patterns):
    """Falls back to ax.Click when InvokePattern is not supported."""
    from operator_use.computer.tools.macos import computer

    element = MagicMock()
    mock_ax.ElementAtPosition.return_value = element
    mock_ax.GetRootControl.return_value = MagicMock()
    mock_ax_patterns.InvokePattern.IsSupported.return_value = False

    result = run(computer.ainvoke(action="click", loc=[100, 200]))

    mock_ax.Click.assert_called_once_with(100, 200)
    assert result.success


def test_click_falls_back_when_no_element(mock_ax, mock_ax_patterns):
    """Falls back to ax.Click when no element found at position."""
    from operator_use.computer.tools.macos import computer

    mock_ax.ElementAtPosition.return_value = None
    mock_ax.GetRootControl.return_value = MagicMock()

    result = run(computer.ainvoke(action="click", loc=[100, 200]))

    mock_ax.Click.assert_called_once_with(100, 200)
    assert result.success


def test_right_click_always_uses_coordinates(mock_ax, mock_ax_patterns):
    """Right click always uses coordinate path -- context menus need screen position."""
    from operator_use.computer.tools.macos import computer

    result = run(computer.ainvoke(action="click", loc=[100, 200], button="right"))

    mock_ax_patterns.InvokePattern.IsSupported.assert_not_called()
    mock_ax.RightClick.assert_called_once_with(100, 200)
    assert result.success


def test_double_click_always_uses_coordinates(mock_ax, mock_ax_patterns):
    """Double click always uses coordinate path."""
    from operator_use.computer.tools.macos import computer

    result = run(computer.ainvoke(action="click", loc=[100, 200], clicks=2))

    mock_ax_patterns.InvokePattern.IsSupported.assert_not_called()
    mock_ax.DoubleClick.assert_called_once_with(100, 200)
    assert result.success


def test_click_falls_back_when_invoke_returns_false(mock_ax, mock_ax_patterns):
    """When Invoke() returns False, fall back to coordinate click."""
    from operator_use.computer.tools.macos import computer

    element = MagicMock()
    mock_ax.ElementAtPosition.return_value = element
    mock_ax.GetRootControl.return_value = MagicMock()
    mock_ax_patterns.InvokePattern.IsSupported.return_value = True
    mock_ax_patterns.InvokePattern.return_value.Invoke.return_value = False

    result = run(computer.ainvoke(action="click", loc=[100, 200]))

    mock_ax.Click.assert_called_once_with(100, 200)
    assert result.success


def test_invoke_exception_falls_back(mock_ax, mock_ax_patterns):
    """If Invoke() raises, fall back silently to ax.Click."""
    from operator_use.computer.tools.macos import computer

    element = MagicMock()
    mock_ax.ElementAtPosition.return_value = element
    mock_ax.GetRootControl.return_value = MagicMock()
    mock_ax_patterns.InvokePattern.IsSupported.return_value = True
    mock_ax_patterns.InvokePattern.return_value.Invoke.side_effect = Exception("AX error")

    result = run(computer.ainvoke(action="click", loc=[100, 200]))

    mock_ax.Click.assert_called_once_with(100, 200)
    assert result.success


# --- Cursorless type tests ---


def test_type_uses_value_pattern_when_supported(mock_ax, mock_ax_patterns):
    """Type should use ValuePattern.SetValue and NOT call ax.Click or ax.TypeText."""
    from operator_use.computer.tools.macos import computer

    element = MagicMock()
    mock_ax.ElementAtPosition.return_value = element
    mock_ax.GetRootControl.return_value = MagicMock()

    vp = MagicMock()
    vp.IsReadOnly = False
    mock_ax_patterns.ValuePattern.IsSupported.return_value = True
    mock_ax_patterns.ValuePattern.return_value = vp

    result = run(computer.ainvoke(action="type", loc=[100, 200], text="hello"))

    mock_ax_patterns.ValuePattern.IsSupported.assert_called_once_with(element)
    vp.SetValue.assert_called_once_with("hello")
    mock_ax.Click.assert_not_called()
    mock_ax.TypeText.assert_not_called()
    assert result.success


def test_type_with_clear_calls_set_value_with_new_text(mock_ax, mock_ax_patterns):
    """Type with clear=True should call SetValue with just the new text."""
    from operator_use.computer.tools.macos import computer

    element = MagicMock()
    mock_ax.ElementAtPosition.return_value = element
    mock_ax.GetRootControl.return_value = MagicMock()

    vp = MagicMock()
    vp.IsReadOnly = False
    vp.Value = "old"
    mock_ax_patterns.ValuePattern.IsSupported.return_value = True
    mock_ax_patterns.ValuePattern.return_value = vp

    result = run(computer.ainvoke(action="type", loc=[100, 200], text="new", clear=True))

    vp.SetValue.assert_called_once_with("new")
    mock_ax.Click.assert_not_called()
    mock_ax.TypeText.assert_not_called()
    assert result.success


def test_type_falls_back_when_value_pattern_is_read_only(mock_ax, mock_ax_patterns):
    """Falls back to Click+TypeText when ValuePattern is ReadOnly."""
    from operator_use.computer.tools.macos import computer

    element = MagicMock()
    mock_ax.ElementAtPosition.return_value = element
    mock_ax.GetRootControl.return_value = MagicMock()

    vp = MagicMock()
    vp.IsReadOnly = True
    mock_ax_patterns.ValuePattern.IsSupported.return_value = True
    mock_ax_patterns.ValuePattern.return_value = vp

    result = run(computer.ainvoke(action="type", loc=[100, 200], text="hello"))

    mock_ax.Click.assert_called_once()
    mock_ax.TypeText.assert_called_once_with("hello")
    assert result.success


def test_type_falls_back_when_caret_position_not_idle(mock_ax, mock_ax_patterns):
    """Falls back to Click+TypeText when caret_position is not idle."""
    from operator_use.computer.tools.macos import computer

    element = MagicMock()
    mock_ax.ElementAtPosition.return_value = element
    mock_ax.GetRootControl.return_value = MagicMock()

    vp = MagicMock()
    vp.IsReadOnly = False
    mock_ax_patterns.ValuePattern.IsSupported.return_value = True
    mock_ax_patterns.ValuePattern.return_value = vp

    result = run(computer.ainvoke(action="type", loc=[100, 200], text="hello", caret_position="end"))

    mock_ax.Click.assert_called_once()
    mock_ax.TypeText.assert_called_once_with("hello")
    assert result.success


def test_type_press_enter_after_set_value(mock_ax, mock_ax_patterns):
    """press_enter=True after ValuePattern.SetValue should fire KeyPress(Return)."""
    from operator_use.computer.tools.macos import computer

    element = MagicMock()
    mock_ax.ElementAtPosition.return_value = element
    mock_ax.GetRootControl.return_value = MagicMock()

    vp = MagicMock()
    vp.IsReadOnly = False
    mock_ax_patterns.ValuePattern.IsSupported.return_value = True
    mock_ax_patterns.ValuePattern.return_value = vp

    result = run(computer.ainvoke(action="type", loc=[100, 200], text="hello", press_enter=True))

    vp.SetValue.assert_called_once_with("hello")
    mock_ax.KeyPress.assert_called_once_with(mock_ax.KeyCode.Return)
    mock_ax.Click.assert_not_called()
    assert result.success

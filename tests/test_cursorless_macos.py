"""Tests for cursorless click path in the macOS computer tool."""

import pytest  # ty: ignore[unresolved-import]
from unittest.mock import MagicMock
import asyncio


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

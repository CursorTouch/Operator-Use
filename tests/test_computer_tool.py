from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from operator_use.builtins.tools.computer import ComputerTool
from operator_use.tool.types import ToolContext, ToolInvocation


@dataclass
class _Window:
    name: str
    status: str = "Active"


@dataclass
class _State:
    active_window: _Window
    windows: list[_Window]
    screenshot: bytes | None = None


class _Computer:
    def __init__(self):
        self.calls = []

    def get_state(self, as_bytes: bool = False):
        self.calls.append(("snapshot", as_bytes))
        return _State(
            active_window=_Window("Terminal"),
            windows=[_Window("Browser", "Visible")],
            screenshot=b"png" if as_bytes else None,
        )

    def click(self, loc, button="left", clicks=1):
        self.calls.append(("click", loc, button, clicks))

    def type(self, loc, text, caret_position="idle", clear=False, press_enter=False):
        self.calls.append(("type", loc, text, caret_position, clear, press_enter))

    def wait(self, duration):
        self.calls.append(("wait", duration))

    def app(self, mode="launch", name=None, loc=None, size=None):
        self.calls.append(("app", mode, name, loc, size))
        return f"{mode}:{name or 'active'}"

    def scroll(self, loc=None, orientation="vertical", direction="down", wheel_times=1):
        self.calls.append(("scroll", loc, orientation, direction, wheel_times))

    def move(self, loc):
        self.calls.append(("move", loc))

    def drag(self, loc):
        self.calls.append(("drag", loc))

    def shortcut(self, shortcut):
        self.calls.append(("shortcut", shortcut))


@pytest.mark.asyncio
async def test_computer_tool_snapshot_returns_state_json():
    tool = ComputerTool()
    computer = _Computer()

    result = await tool.execute(
        ToolInvocation(id="1", name="computer", params={"action": "snapshot", "include_screenshot": True}),
        context=ToolContext(computer=computer),
    )

    assert not result.is_error
    data = json.loads(result.content)
    assert data["active_window"]["name"] == "Terminal"
    assert data["screenshot"]["encoding"] == "base64"
    assert computer.calls == [("snapshot", True)]


@pytest.mark.asyncio
async def test_computer_tool_routes_pointer_and_text_actions():
    tool = ComputerTool()
    computer = _Computer()

    click = await tool.execute(
        ToolInvocation(id="1", name="computer", params={"action": "click", "x": 10, "y": 20, "clicks": 2}),
        context=ToolContext(computer=computer),
    )
    typed = await tool.execute(
        ToolInvocation(
            id="2",
            name="computer",
            params={"action": "type", "loc": [30, 40], "text": "hello", "clear": True, "press_enter": True},
        ),
        context=ToolContext(computer=computer),
    )

    assert not click.is_error
    assert not typed.is_error
    assert computer.calls == [
        ("click", (10, 20), "left", 2),
        ("type", (30, 40), "hello", "idle", True, True),
    ]


@pytest.mark.asyncio
async def test_computer_tool_routes_app_and_keyboard_actions():
    tool = ComputerTool()
    computer = _Computer()

    app = await tool.execute(
        ToolInvocation(id="1", name="computer", params={"action": "app", "app_mode": "switch", "name": "Terminal"}),
        context=ToolContext(computer=computer),
    )
    shortcut = await tool.execute(
        ToolInvocation(id="2", name="computer", params={"action": "shortcut", "shortcut": "command+c"}),
        context=ToolContext(computer=computer),
    )

    assert not app.is_error
    assert "switch:Terminal" in app.content
    assert not shortcut.is_error
    assert computer.calls == [
        ("app", "switch", "Terminal", None, None),
        ("shortcut", "command+c"),
    ]


@pytest.mark.asyncio
async def test_computer_tool_validates_required_action_fields():
    tool = ComputerTool()

    result = await tool.execute(
        ToolInvocation(id="1", name="computer", params={"action": "click"}),
        context=ToolContext(computer=_Computer()),
    )

    assert result.is_error
    assert "loc" in result.content

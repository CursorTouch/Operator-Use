"""Windows computer tool — single unified action interface for GUI automation via UIA."""

import asyncio
import json
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator
from operator_use.tools import Tool, ToolResult
from operator_use.computer.windows import uia, vdm


KEY_ALIASES = {
    "backspace": "Back",
    "capslock": "Capital",
    "scrolllock": "Scroll",
    "windows": "Win",
    "win": "Win",
    "command": "Win",
    "option": "Alt",
}


class ComputerTool(BaseModel):
    action: Literal["click", "type", "scroll", "move", "shortcut", "wait", "desktop"] = Field(
        ...,
        description=(
            "Computer action to perform:\n"
            "  click    — Click at (loc) coordinates. Single, double, right-click, or hover.\n"
            "  type     — Click at (loc) then type text. Handles focus, clear, and Enter automatically.\n"
            "  scroll   — Scroll at (loc) or current cursor position. Vertical or horizontal.\n"
            "  move     — Move cursor to (loc) or drag from current position to (loc).\n"
            "  shortcut — Press a keyboard shortcut, e.g. 'ctrl+c', 'alt+tab', 'enter', 'win'.\n"
            "  wait     — Pause for a number of seconds before the next action.\n"
            "  desktop  — Manage Windows virtual desktops: create, remove, rename, or switch.\n"
        ),
    )
    # Coordinates — used by click, type, scroll, move
    loc: Optional[list[int]] = Field(
        default=None,
        description="[x, y] pixel coordinates from the Interactive Elements list. Required for click, type, move. Optional for scroll (omit to scroll at current cursor).",
        examples=[[640, 360], [100, 200]],
    )
    # click
    button: Literal["left", "right", "middle"] = Field(
        default="left",
        description="Mouse button: 'left' for standard clicks, 'right' for context menus, 'middle' for middle-click (action=click).",
    )
    clicks: int = Field(
        default=1,
        description="Number of clicks: 1=single click, 2=double click (open files/folders), 0=hover only (action=click).",
        examples=[1, 2],
    )
    # type
    text: Optional[str] = Field(
        default=None,
        description="Text to type into the focused field (action=type).",
        examples=["hello world", "user@example.com"],
    )
    clear: bool = Field(
        default=False,
        description="Select all and replace existing text before typing (action=type).",
    )
    caret_position: Literal["start", "idle", "end"] = Field(
        default="idle",
        description="Where to position the cursor before typing: 'start', 'end', or 'idle' (leave as-is) (action=type).",
    )
    press_enter: bool = Field(
        default=False,
        description="Press Enter after typing to submit the input (action=type).",
    )
    # scroll
    axis: Literal["vertical", "horizontal"] = Field(
        default="vertical",
        description="Scroll axis: 'vertical' for up/down, 'horizontal' for left/right (action=scroll).",
    )
    direction: Literal["up", "down", "left", "right"] = Field(
        default="down",
        description="Scroll direction. Use up/down for vertical, left/right for horizontal (action=scroll).",
    )
    wheel_times: int = Field(
        default=1,
        description="Number of scroll increments. Each scrolls ~3-5 lines. Use 3-5 for moderate, 10+ for large jumps (action=scroll).",
        examples=[1, 3, 5, 10],
    )
    # move
    drag: bool = Field(
        default=False,
        description="Hold left mouse button and drag from current position to loc (action=move). False = move/hover only.",
    )
    # shortcut
    shortcut: Optional[str] = Field(
        default=None,
        description="Keyboard shortcut using '+' to combine keys (action=shortcut). Examples: 'ctrl+c', 'alt+tab', 'ctrl+shift+n', 'win', 'enter', 'escape'.",
        examples=["ctrl+c", "ctrl+v", "alt+tab", "win", "enter", "escape"],
    )
    # wait
    duration: Optional[int] = Field(
        default=None,
        description="Seconds to pause (action=wait). Use 2-3s for UI transitions, 5s for app launches, 10s+ for installs.",
        examples=[2, 5, 10],
    )
    # desktop
    desktop_action: Optional[Literal["create", "remove", "rename", "switch"]] = Field(
        default=None,
        description="Virtual desktop operation: create, remove, rename, or switch (action=desktop).",
    )
    desktop_name: Optional[str] = Field(
        default=None,
        description="Target desktop name. Required for remove, rename, and switch (action=desktop).",
        examples=["Desktop 1", "Work", "Research"],
    )
    new_name: Optional[str] = Field(
        default=None,
        description="New name when renaming a desktop (action=desktop, desktop_action=rename).",
        examples=["My Workspace", "Project Alpha"],
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_params(cls, data: dict) -> dict:
        if not isinstance(data, dict):
            return data
        # list fields: parse JSON strings
        for field in ("loc",):
            v = data.get(field)
            if v is None or v == "null":
                data[field] = None
            elif isinstance(v, str):
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        data[field] = parsed
                except (json.JSONDecodeError, ValueError):
                    pass
        # bool fields
        for field in ("clear", "press_enter", "drag"):
            v = data.get(field)
            if isinstance(v, str):
                data[field] = v.lower() not in ("false", "0", "no", "null", "none", "")
        # int fields
        for field in ("clicks", "wheel_times"):
            v = data.get(field)
            if isinstance(v, str):
                try:
                    data[field] = int(v)
                except (ValueError, TypeError):
                    pass
        # nullable int fields
        for field in ("duration",):
            v = data.get(field)
            if v is None or v == "null":
                data[field] = None
            elif isinstance(v, str):
                try:
                    data[field] = int(v)
                except (ValueError, TypeError):
                    pass
        # nullable str fields
        for field in ("shortcut", "text", "desktop_name", "new_name", "desktop_action"):
            v = data.get(field)
            if v == "null":
                data[field] = None
        return data


@Tool(
    name="computer",
    description=(
        "Control the Windows desktop. "
        "The current desktop state (active window, open windows, interactive elements with coordinates, scrollable elements) "
        "is provided automatically before each call — use element coordinates from the state for click, type, scroll, and move."
    ),
    model=ComputerTool,
)
async def computer(
    action: str,
    loc: Optional[list[int]] = None,
    button: str = "left",
    clicks: int = 1,
    text: Optional[str] = None,
    clear: bool = False,
    caret_position: str = "idle",
    press_enter: bool = False,
    axis: str = "vertical",
    direction: str = "down",
    wheel_times: int = 1,
    drag: bool = False,
    shortcut: Optional[str] = None,
    duration: Optional[int] = None,
    desktop_action: Optional[str] = None,
    desktop_name: Optional[str] = None,
    new_name: Optional[str] = None,
    **kwargs,
) -> ToolResult:
    match action:
        case "click":
            if not loc:
                return ToolResult.error_result("loc is required for click.")
            x, y = loc[0], loc[1]
            if clicks == 0:
                uia.SetCursorPos(x, y)
                return ToolResult.success_result(f"Moved cursor to ({x},{y}).")
            match button:
                case "left":
                    if clicks >= 2:
                        uia.DoubleClick(x, y)
                    else:
                        uia.Click(x, y)
                case "right":
                    uia.RightClick(x, y)
                case "middle":
                    uia.MiddleClick(x, y)
            labels = {1: "Single", 2: "Double", 3: "Triple"}
            return ToolResult.success_result(
                f"{labels.get(clicks, str(clicks))} {button} clicked at ({x},{y})."
            )

        case "type":
            if not loc:
                return ToolResult.error_result("loc is required for type.")
            if text is None:
                return ToolResult.error_result("text is required for type.")
            x, y = loc[0], loc[1]
            uia.Click(x, y)
            if caret_position == "start":
                uia.SendKeys("{Home}", waitTime=0.05)
            elif caret_position == "end":
                uia.SendKeys("{End}", waitTime=0.05)
            if clear:
                await asyncio.sleep(0.5)
                uia.SendKeys("{Ctrl}a", waitTime=0.05)
                uia.SendKeys("{Back}", waitTime=0.05)
            escaped = uia._escape_text_for_sendkeys(text)
            uia.SendKeys(escaped, interval=0.01, waitTime=0.05)
            if press_enter:
                uia.SendKeys("{Enter}", waitTime=0.05)
            return ToolResult.success_result(f"Typed at ({x},{y}).")

        case "scroll":
            if loc:
                uia.MoveTo(loc[0], loc[1])
            match axis:
                case "vertical":
                    if direction == "up":
                        uia.WheelUp(wheel_times)
                    elif direction == "down":
                        uia.WheelDown(wheel_times)
                    else:
                        return ToolResult.error_result(
                            'Invalid direction for vertical scroll. Use "up" or "down".'
                        )
                case "horizontal":
                    if direction == "left":
                        uia.WheelLeft(wheel_times)
                    elif direction == "right":
                        uia.WheelRight(wheel_times)
                    else:
                        return ToolResult.error_result(
                            'Invalid direction for horizontal scroll. Use "left" or "right".'
                        )
                case _:
                    return ToolResult.error_result('Invalid axis. Use "vertical" or "horizontal".')
            return ToolResult.success_result(f"Scrolled {axis} {direction} by {wheel_times}.")

        case "move":
            if not loc:
                return ToolResult.error_result("loc is required for move.")
            x, y = loc[0], loc[1]
            if drag:
                cx, cy = uia.GetCursorPos()
                uia.DragTo(cx, cy, x, y)
                return ToolResult.success_result(f"Dragged to ({x},{y}).")
            uia.MoveTo(x, y, moveSpeed=10)
            return ToolResult.success_result(f"Moved to ({x},{y}).")

        case "shortcut":
            if not shortcut:
                return ToolResult.error_result("shortcut is required for shortcut.")
            keys = shortcut.split("+")
            sendkeys_str = ""
            for key in keys:
                key = key.strip()
                if len(key) == 1:
                    sendkeys_str += key
                else:
                    name = KEY_ALIASES.get(key.lower(), key)
                    sendkeys_str += "{" + name + "}"
            uia.SendKeys(sendkeys_str, interval=0.01)
            return ToolResult.success_result(f"Pressed {shortcut}.")

        case "wait":
            if duration is None:
                return ToolResult.error_result("duration is required for wait.")
            await asyncio.sleep(duration)
            return ToolResult.success_result(f"Waited for {duration} seconds.")

        case "desktop":
            if not desktop_action:
                return ToolResult.error_result(
                    "desktop_action is required for desktop (create, remove, rename, switch)."
                )
            try:
                match desktop_action:
                    case "create":
                        created_name = vdm.create_desktop(desktop_name)
                        return ToolResult.success_result(f"Created desktop: '{created_name}'")
                    case "remove":
                        if not desktop_name:
                            return ToolResult.error_result("desktop_name is required for remove.")
                        vdm.remove_desktop(desktop_name)
                        return ToolResult.success_result(f"Removed desktop '{desktop_name}'")
                    case "rename":
                        if not desktop_name or not new_name:
                            return ToolResult.error_result(
                                "desktop_name and new_name are required for rename."
                            )
                        vdm.rename_desktop(desktop_name, new_name)
                        return ToolResult.success_result(
                            f"Renamed '{desktop_name}' to '{new_name}'"
                        )
                    case "switch":
                        if not desktop_name:
                            return ToolResult.error_result("desktop_name is required for switch.")
                        vdm.switch_desktop(desktop_name)
                        return ToolResult.success_result(f"Switched to desktop '{desktop_name}'")
                    case _:
                        return ToolResult.error_result(
                            f"Unknown desktop_action: {desktop_action!r}. Use create, remove, rename, or switch."
                        )
            except Exception as e:
                return ToolResult.error_result(str(e))

        case _:
            return ToolResult.error_result(f"Unknown action: {action!r}.")

"""macOS computer tool — single unified action interface for GUI automation via ax."""

import asyncio
import json
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator
from operator_use.tools import Tool, ToolResult
from operator_use.computer.macos import ax


KEY_ALIASES = {
    "ctrl": "command",
    "cmd": "command",
    "command": "command",
    "win": "command",
    "alt": "option",
    "opt": "option",
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
            "  shortcut — Press a keyboard shortcut, e.g. 'command+c', 'command+tab', 'enter'. ctrl/win map to command, alt maps to option.\n"
            "  wait     — Pause for a number of seconds before the next action.\n"
            "  desktop  — Manage macOS Spaces (virtual desktops): create, remove, or switch. rename is not supported on macOS.\n"
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
        description="Keyboard shortcut using '+' to combine keys (action=shortcut). Examples: 'command+c', 'command+tab', 'command+shift+n', 'enter', 'escape'. ctrl/win map to command, alt maps to option.",
        examples=["command+c", "command+v", "command+tab", "enter", "escape"],
    )
    # wait
    duration: Optional[int] = Field(
        default=None,
        description="Seconds to pause (action=wait). Use 2-3s for UI transitions, 5s for app launches, 10s+ for installs.",
        examples=[2, 5, 10],
    )
    # desktop (Spaces)
    desktop_action: Optional[Literal["create", "remove", "rename", "switch"]] = Field(
        default=None,
        description="Space operation: create (new Space), remove (current Space), switch (by number or direction). rename is not supported on macOS (action=desktop).",
    )
    desktop_name: Optional[str] = Field(
        default=None,
        description="For switch: Space number ('1'-'9') or direction ('left'/'right'/'next'/'previous') (action=desktop).",
        examples=["1", "2", "left", "right", "next"],
    )
    new_name: Optional[str] = Field(
        default=None,
        description="Not used on macOS — Spaces are identified by number, not name.",
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
        "Control the macOS desktop. "
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
                ax.SetCursorPos(x, y)
                return ToolResult.success_result(f"Moved cursor to ({x},{y}).")
            ax.MoveTo(x, y)
            await asyncio.sleep(0.05)
            match button:
                case "right":
                    ax.RightClick(x, y)
                case "middle":
                    ax.MiddleClick(x, y)
                case "left":
                    if clicks >= 2:
                        ax.DoubleClick(x, y)
                    else:
                        ax.Click(x, y)
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
            ax.Click(x, y)
            await asyncio.sleep(0.05)
            if clear:
                ax.HotKey("command", "a")
                await asyncio.sleep(0.05)
                ax.KeyPress(ax.KeyCode.Delete)
            if caret_position == "start":
                ax.HotKey("command", "left")
                await asyncio.sleep(0.05)
            elif caret_position == "end":
                ax.HotKey("command", "right")
                await asyncio.sleep(0.05)
            ax.TypeText(text)
            if press_enter:
                await asyncio.sleep(0.05)
                ax.KeyPress(ax.KeyCode.Return)
            return ToolResult.success_result(f"Typed at ({x},{y}).")

        case "scroll":
            if loc:
                ax.MoveTo(loc[0], loc[1])
            if axis == "vertical":
                if direction == "up":
                    ax.WheelUp(clicks=wheel_times)
                elif direction == "down":
                    ax.WheelDown(clicks=wheel_times)
                else:
                    return ToolResult.error_result(
                        'Invalid direction for vertical scroll. Use "up" or "down".'
                    )
            elif axis == "horizontal":
                if direction == "left":
                    ax.WheelLeft(clicks=wheel_times)
                elif direction == "right":
                    ax.WheelRight(clicks=wheel_times)
                else:
                    return ToolResult.error_result(
                        'Invalid direction for horizontal scroll. Use "left" or "right".'
                    )
            else:
                return ToolResult.error_result('Invalid axis. Use "vertical" or "horizontal".')
            return ToolResult.success_result(f"Scrolled {axis} {direction} by {wheel_times}.")

        case "move":
            if not loc:
                return ToolResult.error_result("loc is required for move.")
            x, y = loc[0], loc[1]
            if drag:
                cx, cy = ax.GetCursorPos()
                ax.DragTo(cx, cy, x, y)
                return ToolResult.success_result(f"Dragged to ({x},{y}).")
            ax.MoveTo(x, y)
            return ToolResult.success_result(f"Moved to ({x},{y}).")

        case "shortcut":
            if not shortcut:
                return ToolResult.error_result("shortcut is required for shortcut.")
            keys = shortcut.split("+")
            mapped = [KEY_ALIASES.get(k.strip().lower(), k.strip()) for k in keys]
            ax.HotKey(*mapped)
            return ToolResult.success_result(f"Pressed {shortcut}.")

        case "wait":
            if duration is None:
                return ToolResult.error_result("duration is required for wait.")
            await asyncio.sleep(duration)
            return ToolResult.success_result(f"Waited for {duration} seconds.")

        case "desktop":
            if not desktop_action:
                return ToolResult.error_result(
                    "desktop_action is required for desktop (create, remove, switch)."
                )
            try:
                match desktop_action:
                    case "create":
                        script = "\n".join(
                            [
                                'tell application "System Events"',
                                "    key code 126 using {control down}",
                                "    delay 1.0",
                                "end tell",
                                "",
                                'tell application "System Events" to tell process "Dock"',
                                "    click button 1 of group 2 of group 1 of group 1",
                                "end tell",
                                "",
                                "delay 0.5",
                                "",
                                'tell application "System Events"',
                                "    key code 53",
                                "end tell",
                            ]
                        )
                        response, status = ax.ExecuteCommand(script, mode="osascript", timeout=15)
                        if status == 0:
                            return ToolResult.success_result(
                                "Created a new Space via Mission Control."
                            )
                        return ToolResult.error_result(f"Error creating Space: {response}")

                    case "remove":
                        script = "\n".join(
                            [
                                'tell application "System Events"',
                                "    key code 126 using {control down}",
                                "    delay 1.0",
                                "end tell",
                                "",
                                'tell application "System Events" to tell process "Dock"',
                                "    set mcGroup to group 1 of group 1",
                                "    set spacesBar to list 1 of group 2 of mcGroup",
                                "    set allSpaces to buttons of spacesBar",
                                "    if (count of allSpaces) is less than or equal to 1 then",
                                '        error "Cannot remove the last remaining Space."',
                                "    end if",
                                "    repeat with sp in allSpaces",
                                '        if value of attribute "AXIsSelected" of sp is true then',
                                '            perform action "AXRemoveDesktop" of sp',
                                "            exit repeat",
                                "        end if",
                                "    end repeat",
                                "end tell",
                                "",
                                "delay 0.5",
                                "",
                                'tell application "System Events"',
                                "    key code 53",
                                "end tell",
                            ]
                        )
                        response, status = ax.ExecuteCommand(script, mode="osascript", timeout=15)
                        if status == 0:
                            return ToolResult.success_result("Removed the current Space.")
                        return ToolResult.error_result(f"Error removing Space: {response}")

                    case "rename":
                        return ToolResult.error_result(
                            "Renaming Spaces is not supported on macOS. Spaces are identified by their number."
                        )

                    case "switch":
                        if not desktop_name:
                            return ToolResult.error_result(
                                "desktop_name is required for switch. "
                                "Use a number ('1'-'9') or direction ('left', 'right', 'next', 'previous')."
                            )
                        name = desktop_name.strip().lower()
                        if name in ("left", "previous"):
                            script = 'tell application "System Events" to key code 123 using {control down}'
                        elif name in ("right", "next"):
                            script = 'tell application "System Events" to key code 124 using {control down}'
                        elif name.isdigit():
                            space_num = int(name)
                            if space_num < 1 or space_num > 9:
                                return ToolResult.error_result(
                                    f"Space number must be between 1 and 9. Got: {space_num}"
                                )
                            key_codes = {
                                1: 18,
                                2: 19,
                                3: 20,
                                4: 21,
                                5: 23,
                                6: 22,
                                7: 26,
                                8: 28,
                                9: 25,
                            }
                            key_code = key_codes[space_num]
                            script = f'tell application "System Events" to key code {key_code} using {{control down}}'
                        else:
                            return ToolResult.error_result(
                                f"Invalid desktop_name '{desktop_name}'. "
                                "Use a number (1-9) or direction ('left', 'right', 'next', 'previous')."
                            )
                        response, status = ax.ExecuteCommand(script, mode="osascript")
                        if status == 0:
                            return ToolResult.success_result(f"Switched to Space ({desktop_name}).")
                        return ToolResult.error_result(
                            f"Error switching Space: {response}. "
                            "Ensure shortcuts are enabled in System Settings > Keyboard > Shortcuts > Mission Control."
                        )

                    case _:
                        return ToolResult.error_result(
                            f"Unknown desktop_action: {desktop_action!r}. Use create, remove, or switch."
                        )
            except Exception as e:
                return ToolResult.error_result(str(e))

        case _:
            return ToolResult.error_result(f"Unknown action: {action!r}.")

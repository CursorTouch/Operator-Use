from __future__ import annotations

import base64
import dataclasses
import json
from enum import Enum
from typing import Any, Optional
from operator_use.message.types import UserMessage
from pydantic import BaseModel, Field, model_validator

from operator_use.tool.types import Tool, ToolContext, ToolExecutionMode, ToolInvocation, ToolKind, ToolResult


class ComputerAction(str, Enum):
    open = "open"
    close = "close"
    snapshot = "snapshot"
    click = "click"
    type = "type"
    wait = "wait"
    app = "app"
    scroll = "scroll"
    move = "move"
    drag = "drag"
    shortcut = "shortcut"


class AppMode(str, Enum):
    launch = "launch"
    switch = "switch"
    resize = "resize"
    move = "move"


class MouseButton(str, Enum):
    left = "left"
    right = "right"
    middle = "middle"


class ScrollOrientation(str, Enum):
    vertical = "vertical"
    horizontal = "horizontal"


class ScrollDirection(str, Enum):
    up = "up"
    down = "down"
    left = "left"
    right = "right"


class CaretPosition(str, Enum):
    start = "start"
    idle = "idle"
    end = "end"


class ComputerSchema(BaseModel):
    action: ComputerAction = Field(
        description=(
            "Computer action to perform: open (enable desktop access), "
            "close (release desktop access), snapshot, click, type, wait, app, "
            "scroll, move, drag, or shortcut."
        )
    )
    loc: tuple[int, int] | None = Field(default=None, description="Target screen coordinate as [x, y].")
    x: int | None = Field(default=None, description="Target x coordinate. Used when loc is omitted.")
    y: int | None = Field(default=None, description="Target y coordinate. Used when loc is omitted.")
    text: str | None = Field(default=None, description="Text to type. Required for action=type.")
    duration: float = Field(default=1.0, ge=0, le=60, description="Seconds to wait for action=wait.")
    button: MouseButton = Field(default=MouseButton.left, description="Mouse button for action=click.")
    clicks: int = Field(default=1, ge=0, le=3, description="Number of clicks. Use 0 to only move the pointer.")
    clear: bool = Field(default=False, description="Clear focused text before typing.")
    press_enter: bool = Field(default=False, description="Press Enter after typing.")
    caret_position: CaretPosition = Field(default=CaretPosition.idle, description="Caret movement before typing.")
    app_mode: AppMode = Field(default=AppMode.launch, description="Application operation for action=app.")
    name: str | None = Field(default=None, description="Application name or bundle/app id for action=app.")
    size: tuple[int, int] | None = Field(default=None, description="Window size as [width, height] for app resize.")
    orientation: ScrollOrientation = Field(default=ScrollOrientation.vertical, description="Scroll axis.")
    direction: ScrollDirection = Field(default=ScrollDirection.down, description="Scroll direction.")
    wheel_times: int = Field(default=1, ge=1, le=20, description="Number of wheel ticks for action=scroll.")
    shortcut: str | None = Field(default=None, description="Keyboard shortcut such as command+c or ctrl+c.")
    include_screenshot: bool = Field(default=False, description="Include screenshot bytes in action=snapshot output.")
    annotate: bool = Field(default=False, description="Request annotated screenshots when supported.")
    accessibility: bool = Field(default=True, description="Include accessibility tree data when supported.")

    @model_validator(mode="after")
    def _check_action_fields(self) -> ComputerSchema:
        loc = self.loc or ((self.x, self.y) if self.x is not None and self.y is not None else None)
        if self.action in {ComputerAction.click, ComputerAction.type, ComputerAction.move, ComputerAction.drag} and loc is None:
            raise ValueError("'loc' or both 'x' and 'y' are required for this action")
        if self.action == ComputerAction.type and self.text is None:
            raise ValueError("'text' is required when action='type'")
        if self.action == ComputerAction.shortcut and not self.shortcut:
            raise ValueError("'shortcut' is required when action='shortcut'")
        if self.action == ComputerAction.app:
            if self.app_mode in {AppMode.launch, AppMode.switch} and not self.name:
                raise ValueError("'name' is required when action='app' with launch or switch")
            if self.app_mode == AppMode.resize and self.size is None:
                raise ValueError("'size' is required when action='app' with resize")
            if self.app_mode == AppMode.move and loc is None:
                raise ValueError("'loc' or both 'x' and 'y' are required when action='app' with move")
        return self


class ComputerTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="computer",
            description=(
                "Control the local desktop through one action-based computer tool. "
                "Use open to enable desktop access (required before any other action) and "
                "close to release it. Use snapshot to inspect the screen, "
                "click/type/scroll/move/drag for pointer and text input, "
                "shortcut for keyboard shortcuts, wait for delays, and app for "
                "launching, switching, resizing, or moving applications."
            ),
            schema=ComputerSchema,
            kind=ToolKind.Execute,
            execution_mode=ToolExecutionMode.Sequential,
        )
        self._desktop: Any | None = None

    def is_available(self, context) -> bool:
        sm = context.settings_manager
        if sm is not None and sm.settings.computer_use_enabled is False:
            return False
        return True

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        async def _update(text: str) -> None:
            if tool_execution_update_callback:
                await tool_execution_update_callback(ToolResult.ok(invocation.id, text))

        try:
            params = ComputerSchema.model_validate(invocation.params)

            # open / close — the desktop object itself is the open/closed state.
            match params.action:
                case ComputerAction.open:
                    await _update("🖥️ Opening desktop…")
                    self._desktop = self._get_desktop(context, params)
                    return ToolResult.ok(id=invocation.id, content="Desktop access enabled.")

                case ComputerAction.close:
                    if self._desktop is None:
                        return ToolResult.ok(id=invocation.id, content="Desktop is not open.")
                    await _update("🖥️ Closing desktop…")
                    self._desktop = None
                    return ToolResult.ok(id=invocation.id, content="Desktop access released.")

            # Guard: require an explicit open before any desktop interaction.
            if self._desktop is None:
                return ToolResult.error(
                    id=invocation.id,
                    content="Desktop is not accessible. Use action='open' to enable desktop control first.",
                )

            loc = self._loc(params)
            match params.action:
                case ComputerAction.snapshot:
                    await _update("🖥️ Taking desktop snapshot…")
                case ComputerAction.click:
                    btn = params.button.value
                    coord = f"({loc[0]}, {loc[1]})" if loc else ""
                    clicks = f" ×{params.clicks}" if params.clicks > 1 else ""
                    await _update(f"🖱️ {btn.capitalize()} clicking{clicks} at {coord}…")
                case ComputerAction.type:
                    preview = (params.text or "")[:40]
                    preview += "…" if len(params.text or "") > 40 else ""
                    await _update(f"⌨️ Typing \"{preview}\"…")
                case ComputerAction.wait:
                    await _update(f"⏳ Waiting {params.duration:g}s…")
                case ComputerAction.app:
                    label = params.name or params.app_mode.value
                    await _update(f"🖥️ App {params.app_mode.value}: {label}…")
                case ComputerAction.scroll:
                    await _update(f"🖱️ Scrolling {params.direction.value} {params.wheel_times}×…")
                case ComputerAction.move:
                    coord = f"({loc[0]}, {loc[1]})" if loc else ""
                    await _update(f"🖱️ Moving pointer to {coord}…")
                case ComputerAction.drag:
                    coord = f"({loc[0]}, {loc[1]})" if loc else ""
                    await _update(f"🖱️ Dragging to {coord}…")
                case ComputerAction.shortcut:
                    await _update(f"⌨️ Shortcut {params.shortcut}…")
            desktop = self._get_desktop(context, params)
            content = self._run_action(desktop, params)
            return ToolResult.ok(id=invocation.id, content=content)
        except Exception as exc:
            return ToolResult.error(id=invocation.id, content=f"computer: {exc}")
    
    @property
    async def state_message(self)->Optional[UserMessage]:
        """Return a UserMessage with the current desktop state, or None if closed.

        Called by EphemeralInjector just before each LLM API call.  The message
        is injected into the context for that single call and then stripped —
        it is never persisted in state.messages or the session JSONL.
        Screenshots are excluded to keep token cost low; the model can request
        them explicitly via action='snapshot' with include_screenshot=True.
        """
        if self._desktop is None:
            return None
        try:
            state = self._desktop.get_state(as_bytes=False)
            state_text = json.dumps(self._to_jsonable(state), indent=2)
            return UserMessage.text(f"[Current desktop state]\n{state_text}")
        except Exception:
            return None

    def _get_desktop(self, context: ToolContext | None, params: ComputerSchema) -> Any:
        if context is not None and context.computer is not None:
            return context.computer
        if self._desktop is None:
            from operator_use import computer

            self._desktop = computer.Desktop(
                use_vision=params.include_screenshot,
                use_annotation=params.annotate,
                use_accessibility=params.accessibility,
            )
        return self._desktop

    def _run_action(self, desktop: Any, params: ComputerSchema) -> str:
        loc = self._loc(params)
        match params.action:
            case ComputerAction.snapshot:
                state = desktop.get_state(as_bytes=params.include_screenshot)
                return json.dumps(self._to_jsonable(state), indent=2)
            case ComputerAction.click:
                assert loc is not None
                desktop.click(loc, button=params.button.value, clicks=params.clicks)
                return f"Clicked {params.button.value} at {loc[0]},{loc[1]}."
            case ComputerAction.type:
                assert loc is not None
                desktop.type(
                    loc,
                    text=params.text or "",
                    caret_position=params.caret_position.value,
                    clear=params.clear,
                    press_enter=params.press_enter,
                )
                return "Typed text."
            case ComputerAction.wait:
                desktop.wait(params.duration)
                return f"Waited {params.duration:g} seconds."
            case ComputerAction.app:
                result = desktop.app(
                    mode=params.app_mode.value,
                    name=params.name,
                    loc=loc,
                    size=params.size,
                )
                return str(result or "App action completed.")
            case ComputerAction.scroll:
                result = desktop.scroll(
                    loc=loc,
                    orientation=params.orientation.value,
                    direction=params.direction.value,
                    wheel_times=params.wheel_times,
                )
                return str(result or "Scrolled.")
            case ComputerAction.move:
                assert loc is not None
                desktop.move(loc)
                return f"Moved pointer to {loc[0]},{loc[1]}."
            case ComputerAction.drag:
                assert loc is not None
                desktop.drag(loc)
                return f"Dragged pointer to {loc[0]},{loc[1]}."
            case ComputerAction.shortcut:
                desktop.shortcut(params.shortcut or "")
                return f"Pressed shortcut {params.shortcut}."
        return f"Unknown action: {params.action.value}"

    def _loc(self, params: ComputerSchema) -> tuple[int, int] | None:
        if params.loc is not None:
            return params.loc
        if params.x is not None and params.y is not None:
            return (params.x, params.y)
        return None

    def _to_jsonable(self, value: Any) -> Any:
        if dataclasses.is_dataclass(value):
            return {
                field.name: self._to_jsonable(getattr(value, field.name))
                for field in dataclasses.fields(value)
            }
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, bytes):
            return {
                "type": "bytes",
                "encoding": "base64",
                "data": base64.b64encode(value).decode("ascii"),
            }
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, dict):
            return {str(key): self._to_jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._to_jsonable(item) for item in value]
        if hasattr(value, "model_dump"):
            return self._to_jsonable(value.model_dump())
        if hasattr(value, "to_string"):
            return value.to_string()
        return str(value)


tool = ComputerTool()

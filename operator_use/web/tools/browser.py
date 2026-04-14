"""Browser tool — single unified action interface for web automation via CDP."""

from operator_use.tools import Tool, ToolResult
from pydantic import BaseModel, Field, model_validator
from markdownify import markdownify
from typing import Literal, Optional
from asyncio import sleep
from pathlib import Path
from os import getcwd
import httpx
import json


class BrowserTool(BaseModel):
    action: Literal[
        "goto",
        "back",
        "forward",
        "click",
        "type",
        "key",
        "scroll",
        "menu",
        "upload",
        "tab",
        "wait",
        "script",
        "scrape",
        "download",
    ] = Field(
        ...,
        description=(
            "Browser action to perform:\n"
            "  goto       — Navigate to a URL (requires url).\n"
            "  back       — Go to the previous page in browser history.\n"
            "  forward    — Go to the next page in browser history.\n"
            "  click      — Click at (x, y) coordinates from the Interactive Elements list.\n"
            "  type       — Click at (x, y) then type text. Optionally clear existing text and submit with Enter.\n"
            "  key        — Press a key or combination, e.g. 'Enter', 'Control+A', 'Escape' (requires text).\n"
            "  scroll     — Scroll the page or element at (x, y). Omit x/y to scroll the whole page.\n"
            "  menu       — Select options in a <select> dropdown at (x, y) by visible label text.\n"
            "  upload     — Upload files to a file input at (x, y). Files must exist in ./uploads.\n"
            "  tab        — Manage tabs: tab_mode=open|close|switch. switch requires tab_index.\n"
            "  wait       — Pause for a number of seconds (requires time).\n"
            "  script     — Execute JavaScript on the current page. Wrap in IIFE with try-catch.\n"
            "  scrape     — Extract the current page as markdown.\n"
            "  download   — Download a file from url and save it as filename in the downloads directory.\n"
        ),
    )
    # Navigation
    url: Optional[str] = Field(
        default=None, description="Full URL including protocol. Required for goto and download."
    )
    # Coordinates
    x: Optional[int] = Field(
        default=None,
        description="X coordinate. Required for click, type, scroll (optional), menu, upload.",
    )
    y: Optional[int] = Field(
        default=None,
        description="Y coordinate. Required for click, type, scroll (optional), menu, upload.",
    )
    # Type / key
    text: Optional[str] = Field(
        default=None,
        description="Text to type (action=type) or key/combo to press (action=key), e.g. 'Control+A'.",
    )
    clear: bool = Field(
        default=False, description="Clear existing field content before typing (action=type)."
    )
    press_enter: bool = Field(
        default=False, description="Press Enter after typing to submit (action=type)."
    )
    # Scroll
    direction: Literal["up", "down"] = Field(
        default="down", description="Scroll direction (action=scroll)."
    )
    amount: int = Field(default=500, description="Pixels to scroll per action (action=scroll).")
    # Key repeat
    times: int = Field(default=1, description="Number of times to press the key (action=key).")
    # Tab management
    tab_mode: Literal["open", "close", "switch"] = Field(
        default="open",
        description="Tab operation: open a new tab, close the current tab, or switch to a tab by index (action=tab).",
    )
    tab_index: Optional[int] = Field(
        default=None,
        description="Zero-based index of the tab to switch to (action=tab, tab_mode=switch).",
    )
    # Wait
    time: Optional[int] = Field(default=None, description="Seconds to pause (action=wait).")
    # Script
    script: Optional[str] = Field(
        default=None,
        description="JavaScript to execute on the page (action=script). Always wrap in IIFE with try-catch.",
    )
    # Scrape
    prompt: Optional[str] = Field(
        default=None,
        description="Optional extraction hint for scrape. If omitted, full page markdown is returned.",
    )
    # Upload
    filenames: Optional[list[str]] = Field(
        default=None, description="Filenames to upload from ./uploads directory (action=upload)."
    )
    # Menu / select
    labels: Optional[list[str]] = Field(
        default=None,
        description="Visible option labels to select in a <select> dropdown (action=menu).",
    )
    # Download
    filename: Optional[str] = Field(
        default=None, description="Local filename to save the downloaded file as (action=download)."
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_params(cls, data: dict) -> dict:
        if not isinstance(data, dict):
            return data
        # bool fields
        for field in ("clear", "press_enter"):
            v = data.get(field)
            if isinstance(v, str):
                data[field] = v.lower() not in ("false", "0", "no", "null", "none", "")
        # int fields
        for field in ("amount", "times"):
            v = data.get(field)
            if isinstance(v, str):
                try:
                    data[field] = int(v)
                except (ValueError, TypeError):
                    pass
        # nullable int fields
        for field in ("x", "y", "tab_index", "time"):
            v = data.get(field)
            if v is None or v == "null":
                data[field] = None
            elif isinstance(v, str):
                try:
                    data[field] = int(v)
                except (ValueError, TypeError):
                    pass
        # nullable str fields
        for field in ("url", "text", "script", "prompt", "filename"):
            v = data.get(field)
            if v == "null":
                data[field] = None
        # nullable list[str] fields: parse JSON strings
        for field in ("filenames", "labels"):
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
        return data


@Tool(
    name="browser",
    description=(
        "Control the web browser. "
        "The current browser state (URL, open tabs, interactive elements with coordinates, informative text, scrollable elements) "
        "is provided automatically before each call — use element coordinates from the state for click, type, scroll, menu, and upload."
    ),
    model=BrowserTool,
)
async def browser(
    action: str,
    url: Optional[str] = None,
    x: Optional[int] = None,
    y: Optional[int] = None,
    text: Optional[str] = None,
    clear: bool = False,
    press_enter: bool = False,
    direction: str = "down",
    amount: int = 500,
    times: int = 1,
    tab_mode: str = "open",
    tab_index: Optional[int] = None,
    time: Optional[int] = None,
    script: Optional[str] = None,
    prompt: Optional[str] = None,
    filenames: Optional[list[str]] = None,
    labels: Optional[list[str]] = None,
    filename: Optional[str] = None,
    **kwargs,
) -> ToolResult:
    browser = kwargs.get("browser")
    if browser is None:
        return ToolResult.error_result(
            "Browser is not available. Ensure browser_use plugin is enabled."
        )
    if browser._client is None:
        await browser.init_browser()
        await browser.init_tabs()
    page = browser.current_page()

    match action:
        case "goto":
            if not url:
                return ToolResult.error_result("url is required for goto.")
            await browser.navigate(url)
            return ToolResult.success_result(f"Navigated to {url}")

        case "back":
            await browser.go_back()
            return ToolResult.success_result("Navigated to previous page.")

        case "forward":
            await browser.go_forward()
            return ToolResult.success_result("Navigated to next page.")

        case "click":
            if x is None or y is None:
                return ToolResult.error_result("x and y are required for click.")
            await page.click_at(x, y)
            await browser._wait_for_page(timeout=8.0)
            return ToolResult.success_result(f"Clicked at ({x}, {y}).")

        case "type":
            if x is None or y is None:
                return ToolResult.error_result("x and y are required for type.")
            if text is None:
                return ToolResult.error_result("text is required for type.")
            await page.click_at(x, y)
            if clear:
                await page.key_press("Control+a")
                await page.key_press("Backspace")
            await page.type_text(text, delay_ms=50)
            if press_enter:
                await page.key_press("Enter")
                await browser._wait_for_page(timeout=8.0)
            return ToolResult.success_result(f"Typed at ({x}, {y}).")

        case "key":
            if not text:
                return ToolResult.error_result(
                    "text is required for key (the key or combination to press, e.g. 'Enter', 'Control+A')."
                )
            for _ in range(times):
                await page.key_press(text)
            return ToolResult.success_result(f"Pressed {text}.")

        case "scroll":
            if x is not None and y is not None:
                await page.scroll_at(x, y, direction, amount)
                return ToolResult.success_result(
                    f"Scrolled {direction} at ({x}, {y}) by {amount}px."
                )
            pos = await page.get_scroll_position()
            scroll_y = pos.get("scrollY", 0)
            max_scroll = pos.get("scrollHeight", 0) - pos.get("innerHeight", 0)
            if direction == "down" and scroll_y >= max_scroll:
                return ToolResult.success_result("Already at the bottom, cannot scroll further.")
            if direction == "up" and scroll_y <= 0:
                return ToolResult.success_result("Already at the top, cannot scroll further.")
            await page.scroll_page(direction, amount)
            return ToolResult.success_result(f"Scrolled {direction} by {amount}px.")

        case "tab":
            match tab_mode:
                case "open":
                    await browser.new_tab()
                    await browser._wait_for_page(timeout=5.0)
                    return ToolResult.success_result("Opened a new blank tab.")
                case "close":
                    tabs = await browser.get_all_tabs()
                    if len(tabs) <= 1:
                        return ToolResult.success_result("Cannot close the last remaining tab.")
                    await browser.close_tab()
                    return ToolResult.success_result("Closed current tab.")
                case "switch":
                    tabs = await browser.get_all_tabs()
                    if tab_index is None or tab_index < 0 or tab_index >= len(tabs):
                        return ToolResult.error_result(
                            f"tab_index {tab_index} out of range. Available tabs: {len(tabs)}"
                        )
                    await browser.switch_tab(tab_index)
                    await browser._wait_for_page(timeout=5.0)
                    return ToolResult.success_result(f"Switched to tab {tab_index}.")
                case _:
                    return ToolResult.error_result(
                        "Invalid tab_mode. Use 'open', 'close', or 'switch'."
                    )

        case "wait":
            if time is None:
                return ToolResult.error_result("time (seconds) is required for wait.")
            await sleep(time)
            return ToolResult.success_result(f"Waited {time}s.")

        case "upload":
            if x is None or y is None:
                return ToolResult.error_result("x and y are required for upload.")
            if not filenames:
                return ToolResult.error_result("filenames is required for upload.")
            files = [str(Path(getcwd()) / "uploads" / fn) for fn in filenames]
            missing = [path for path in files if not Path(path).exists()]
            if missing:
                return ToolResult.error_result(f"Upload files not found: {missing}")
            await page.set_file_input_at(x, y, files)
            return ToolResult.success_result(f"Uploaded {filenames} to element at ({x}, {y}).")

        case "menu":
            if x is None or y is None:
                return ToolResult.error_result("x and y are required for menu.")
            if not labels:
                return ToolResult.error_result("labels is required for menu.")
            await page.select_option_at(x, y, labels)
            return ToolResult.success_result(
                f"Selected {', '.join(labels)} in dropdown at ({x}, {y})."
            )

        case "script":
            if not script:
                return ToolResult.error_result("script is required for script.")
            result = await page.execute_script(script, truncate=True, repair=True)
            return ToolResult.success_result(f"Script result: {result}")

        case "scrape":
            html = await page.get_page_content()
            content = markdownify(html)
            return ToolResult.success_result(f"Page content:\n{content}")

        case "download":
            if not url:
                return ToolResult.error_result("url is required for download.")
            if not filename:
                return ToolResult.error_result("filename is required for download.")
            folder_path = Path(browser.config.downloads_dir)
            async with httpx.AsyncClient() as client:
                response = await client.get(url)
                response.raise_for_status()
            path = folder_path / filename
            with open(path, "wb") as f:
                f.write(response.content)
            return ToolResult.success_result(f"Downloaded {filename} from {url} to {path}.")

        case _:
            return ToolResult.error_result(f"Unknown action: {action!r}.")

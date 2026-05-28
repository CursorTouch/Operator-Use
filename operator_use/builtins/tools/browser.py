from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal

import httpx
from markdownify import markdownify
from pydantic import BaseModel, Field, model_validator
from operator_use.browser import Browser
from operator_use.tool.types import Tool, ToolContext, ToolExecutionMode, ToolInvocation, ToolKind, ToolResult


class BrowserSchema(BaseModel):
    action: Literal[
        "open",
        "close",
        "snapshot",
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
        description=(
            "Browser action: open (launch/connect browser), close (shut down browser), "
            "snapshot, goto, back, forward, click, type, key, scroll, menu, "
            "upload, tab, wait, script, scrape, or download."
        )
    )
    url: str | None = Field(default=None, description="URL for action=goto or action=download.")
    x: int | None = Field(default=None, description="X coordinate for click, type, scroll, menu, or upload.")
    y: int | None = Field(default=None, description="Y coordinate for click, type, scroll, menu, or upload.")
    text: str | None = Field(default=None, description="Text to type or key/combo to press.")
    clear: bool = Field(default=False, description="Clear focused field before typing.")
    press_enter: bool = Field(default=False, description="Press Enter after typing.")
    direction: Literal["up", "down"] = Field(default="down", description="Scroll direction.")
    amount: int = Field(default=500, ge=1, description="Scroll amount in pixels.")
    times: int = Field(default=1, ge=1, le=50, description="Number of key presses for action=key.")
    tab_mode: Literal["open", "close", "switch"] = Field(default="open", description="Tab operation.")
    tab_index: int | None = Field(default=None, description="Zero-based tab index for tab_mode=switch.")
    time: float | None = Field(default=None, ge=0, le=120, description="Seconds to wait.")
    script: str | None = Field(default=None, description="JavaScript to execute for action=script.")
    filenames: list[str] | None = Field(default=None, description="Filenames under ./uploads for action=upload.")
    labels: list[str] | None = Field(default=None, description="Visible option labels for action=menu.")
    filename: str | None = Field(default=None, description="Download destination filename for action=download.")
    browser: Literal["chrome", "edge"] | None = Field(default=None, description="Browser to launch.")
    headless: bool = Field(default=False, description="Launch browser headless when creating a new session.")
    attach_to_existing: bool = Field(default=False, description="Attach to an existing CDP browser on cdp_port.")
    cdp_port: int = Field(default=9222, ge=1, le=65535, description="Chrome DevTools Protocol port.")

    @model_validator(mode="before")
    @classmethod
    def _coerce_params(cls, data):
        if not isinstance(data, dict):
            return data

        for field in ("clear", "press_enter", "headless", "attach_to_existing"):
            value = data.get(field)
            if isinstance(value, str):
                data[field] = value.lower() not in {"false", "0", "no", "null", "none", ""}

        for field in ("x", "y", "amount", "times", "tab_index", "cdp_port"):
            if field not in data:
                continue  # absent → let Pydantic apply the field default
            value = data[field]
            if value is None or value == "null":
                data[field] = None
            elif isinstance(value, str):
                try:
                    data[field] = int(value)
                except ValueError:
                    pass

        if "time" in data:
            value = data["time"]
            if value is None or value == "null":
                data["time"] = None
            elif isinstance(value, str):
                try:
                    data["time"] = float(value)
                except ValueError:
                    pass

        for field in ("filenames", "labels"):
            if field not in data:
                continue  # absent → let Pydantic apply the field default
            value = data[field]
            if value is None or value == "null":
                data[field] = None
            elif isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        data[field] = parsed
                except ValueError:
                    pass

        for field in ("url", "text", "script", "filename", "browser"):
            if data.get(field) == "null":
                data[field] = None

        return data


class BrowserTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="browser",
            description=(
                "Control a CDP browser with one action-based tool. Use open to launch or reconnect "
                "the browser (also resets a crashed/stuck session), close to shut it down, snapshot "
                "to inspect tabs and interactive DOM elements, goto/back/forward for navigation, "
                "click/type/key/scroll for page interaction, tab for tab management, script for "
                "JavaScript, scrape for markdown page content, upload for file inputs, menu for "
                "select boxes, and wait."
            ),
            schema=BrowserSchema,
            kind=ToolKind.Web,
            execution_mode=ToolExecutionMode.Sequential,
        )

    def is_available(self, context: ToolContext) -> bool:
        if context.browser is None:
            return False
        sm = context.settings_manager
        if sm is not None:
            bu = sm.settings.browser_use
            if bu is not None and not bu.enabled:
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
            params = BrowserSchema.model_validate(invocation.params)
            browser = context.browser if context is not None else None

            match params.action:
                case "open":
                    if browser is None:
                        return ToolResult.error(invocation.id, "Browser is not available.")
                    await _update("🌐 Opening browser…")
                    if browser.crashed:
                        await browser.close()
                    await browser.open()
                    tabs = await browser.get_all_tabs()
                    return ToolResult.ok(invocation.id, f"Browser ready. {len(tabs)} tab(s) open.")

                case "close":
                    if browser is None or browser._client is None:
                        return ToolResult.ok(invocation.id, "Browser is not open.")
                    await _update("🌐 Closing browser…")
                    await browser.close()
                    return ToolResult.ok(invocation.id, "Browser closed.")

            # Guard: require an explicit open before any browser interaction.
            if browser is None or browser._client is None:
                return ToolResult.error(
                    invocation.id,
                    "Browser is not open. Use action='open' to launch the browser first.",
                )

            browser = await self._get_browser(browser, params)
            page = browser.current_page()

            match params.action:
                case "snapshot":
                    await _update("🌐 Taking DOM snapshot…")
                    state = await browser.get_state()
                    tabs = await browser.get_all_tabs()
                    return ToolResult.ok(
                        invocation.id,
                        "\n".join(
                            [
                                "Tabs:",
                                *[
                                    f"{tab.id}: {tab.title or '(untitled)'} {tab.url}"
                                    for tab in tabs
                                ],
                                "",
                                state.dom_state.interactive_elements_to_string(),
                                "",
                                state.dom_state.scrollable_elements_to_string(),
                                "",
                                state.dom_state.informative_elements_to_string(),
                            ]
                        ),
                    )

                case "goto":
                    if not params.url:
                        return ToolResult.error(invocation.id, "'url' is required for action='goto'.")
                    await _update(f"🌐 Navigating to {params.url}")
                    await browser.navigate(params.url)
                    return ToolResult.ok(invocation.id, f"Navigated to {params.url}.")

                case "back":
                    await _update("🌐 Going back…")
                    await browser.go_back()
                    return ToolResult.ok(invocation.id, "Navigated back.")

                case "forward":
                    await _update("🌐 Going forward…")
                    await browser.go_forward()
                    return ToolResult.ok(invocation.id, "Navigated forward.")

                case "click":
                    if params.x is None or params.y is None:
                        return ToolResult.error(invocation.id, "'x' and 'y' are required for action='click'.")
                    await _update(f"🖱️ Clicking at ({params.x}, {params.y})…")
                    await page.click_at(params.x, params.y)
                    await browser._wait_for_page(timeout=8.0)
                    return ToolResult.ok(invocation.id, f"Clicked at ({params.x}, {params.y}).")

                case "type":
                    if params.x is None or params.y is None:
                        return ToolResult.error(invocation.id, "'x' and 'y' are required for action='type'.")
                    if params.text is None:
                        return ToolResult.error(invocation.id, "'text' is required for action='type'.")
                    preview = params.text[:40] + ('…' if len(params.text) > 40 else '')
                    await _update(f"⌨️ Typing \"{preview}\"…")
                    await page.click_at(params.x, params.y)
                    if params.clear:
                        await page.key_press("Control+A")
                        await page.key_press("Backspace")
                    await page.type_text(params.text)
                    if params.press_enter:
                        await page.key_press("Enter")
                        await browser._wait_for_page(timeout=8.0)
                    return ToolResult.ok(invocation.id, f"Typed at ({params.x}, {params.y}).")

                case "key":
                    if not params.text:
                        return ToolResult.error(invocation.id, "'text' is required for action='key'.")
                    await _update(f"⌨️ Pressing {params.text}…")
                    for _ in range(params.times):
                        await page.key_press(params.text)
                    return ToolResult.ok(invocation.id, f"Pressed {params.text}.")

                case "scroll":
                    await _update(f"🖱️ Scrolling {params.direction} {params.amount}px…")
                    if params.x is not None and params.y is not None:
                        await page.scroll_at(params.x, params.y, params.direction, params.amount)
                        return ToolResult.ok(
                            invocation.id,
                            f"Scrolled {params.direction} at ({params.x}, {params.y}) by {params.amount}px.",
                        )
                    pos = await page.get_scroll_position()
                    scroll_y = pos.get("scrollY", 0)
                    max_scroll = pos.get("scrollHeight", 0) - pos.get("innerHeight", 0)
                    if params.direction == "down" and scroll_y >= max_scroll:
                        return ToolResult.ok(invocation.id, "Already at the bottom.")
                    if params.direction == "up" and scroll_y <= 0:
                        return ToolResult.ok(invocation.id, "Already at the top.")
                    await page.scroll_page(params.direction, params.amount)
                    return ToolResult.ok(invocation.id, f"Scrolled {params.direction} by {params.amount}px.")

                case "menu":
                    if params.x is None or params.y is None:
                        return ToolResult.error(invocation.id, "'x' and 'y' are required for action='menu'.")
                    if not params.labels:
                        return ToolResult.error(invocation.id, "'labels' is required for action='menu'.")
                    await _update(f"🖱️ Selecting {', '.join(params.labels)}…")
                    await page.select_option_at(params.x, params.y, params.labels)
                    return ToolResult.ok(invocation.id, f"Selected {', '.join(params.labels)}.")

                case "upload":
                    if params.x is None or params.y is None:
                        return ToolResult.error(invocation.id, "'x' and 'y' are required for action='upload'.")
                    if not params.filenames:
                        return ToolResult.error(invocation.id, "'filenames' is required for action='upload'.")
                    await _update(f"📎 Uploading {', '.join(params.filenames or [])}…")
                    upload_root = Path(invocation.cwd or ".").resolve() / "uploads"
                    files = [str(upload_root / filename) for filename in params.filenames]
                    missing = [path for path in files if not Path(path).exists()]
                    if missing:
                        return ToolResult.error(invocation.id, f"Upload files not found: {missing}")
                    await page.set_file_input_at(params.x, params.y, files)
                    return ToolResult.ok(invocation.id, f"Uploaded {params.filenames}.")

                case "tab":
                    return await self._tab_action(invocation.id, browser, params)

                case "wait":
                    if params.time is None:
                        return ToolResult.error(invocation.id, "'time' is required for action='wait'.")
                    await _update(f"⏳ Waiting {params.time:g}s…")
                    await asyncio.sleep(params.time)
                    return ToolResult.ok(invocation.id, f"Waited {params.time:g}s.")

                case "script":
                    if not params.script:
                        return ToolResult.error(invocation.id, "'script' is required for action='script'.")
                    await _update("📜 Running script…")
                    result = await page.execute_script(params.script, truncate=True, repair=True)
                    return ToolResult.ok(invocation.id, f"Script result: {result}")

                case "scrape":
                    await _update("🌐 Scraping page content…")
                    html = await page.get_page_content()
                    return ToolResult.ok(invocation.id, f"Page content:\n{markdownify(html)}")

                case "download":
                    if params.url:
                        await _update(f"⬇️ Downloading {params.filename or 'file'}…")
                    return await self._download(invocation.id, browser, params)

                case _:
                    return ToolResult.error(invocation.id, f"Unknown browser action: {params.action!r}.")
        except Exception as exc:
            return ToolResult.error(invocation.id, f"browser: {exc}")

    async def _get_browser(self, browser: Browser, params: BrowserSchema) -> Browser:
        """Ensure the browser is connected, reconnecting if needed."""
        if browser.crashed:
            try:
                await browser.close()
            except Exception:
                pass
        await browser.open()
        return browser

    async def _tab_action(self, invocation_id: str, browser: Browser, params: BrowserSchema) -> ToolResult:
        match params.tab_mode:
            case "open":
                await browser.new_tab()
                await browser._wait_for_page(timeout=5.0)
                return ToolResult.ok(invocation_id, "Opened a new blank tab.")
            case "close":
                tabs = await browser.get_all_tabs()
                if len(tabs) <= 1:
                    return ToolResult.ok(invocation_id, "Cannot close the last remaining tab.")
                await browser.close_tab()
                return ToolResult.ok(invocation_id, "Closed current tab.")
            case "switch":
                tabs = await browser.get_all_tabs()
                if params.tab_index is None or params.tab_index < 0 or params.tab_index >= len(tabs):
                    return ToolResult.error(
                        invocation_id,
                        f"tab_index {params.tab_index} out of range. Available tabs: {len(tabs)}.",
                    )
                await browser.switch_tab(params.tab_index)
                await browser._wait_for_page(timeout=5.0)
                return ToolResult.ok(invocation_id, f"Switched to tab {params.tab_index}.")

    async def _download(self, invocation_id: str, browser: Browser, params: BrowserSchema) -> ToolResult:
        if not params.url:
            return ToolResult.error(invocation_id, "'url' is required for action='download'.")
        if not params.filename:
            return ToolResult.error(invocation_id, "'filename' is required for action='download'.")
        folder_path = Path(browser.config.downloads_dir)
        folder_path.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient() as client:
            response = await client.get(params.url)
            response.raise_for_status()
        path = folder_path / params.filename
        path.write_bytes(response.content)
        return ToolResult.ok(invocation_id, f"Downloaded {params.filename} from {params.url} to {path}.")


tool = BrowserTool()

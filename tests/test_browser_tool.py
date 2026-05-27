"""Tests for BrowserTool — open/close guard, snapshot, state_message,
and the browser screenshot as_bytes pipeline."""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image as PILImage

from operator_use.builtins.tools.browser import BrowserTool
from operator_use.tool.types import ToolInvocation


# ---------------------------------------------------------------------------
# Helpers — minimal fakes that avoid importing the real Browser class
# ---------------------------------------------------------------------------

def _make_image() -> PILImage.Image:
    img = PILImage.new("RGB", (10, 10), color=(255, 0, 0))
    return img


def _image_to_bytes(img: PILImage.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@dataclass
class _DOMState:
    def interactive_elements_to_string(self) -> str:
        return "[1] <button>Click me</button>"

    def scrollable_elements_to_string(self) -> str:
        return ""

    def informative_elements_to_string(self) -> str:
        return "Page title"


@dataclass
class _BrowserState:
    screenshot: PILImage.Image | bytes | None = None
    dom_state: _DOMState = field(default_factory=_DOMState)


@dataclass
class _Tab:
    id: int
    url: str
    title: str


def _make_browser(screenshot=None):
    """Return an async-capable fake browser object."""
    browser = MagicMock()
    browser.crashed = False  # prevent _get_browser from tearing down the mock
    browser.get_state = AsyncMock(return_value=_BrowserState(screenshot=screenshot))
    browser.get_all_tabs = AsyncMock(return_value=[_Tab(0, "https://example.com", "Example")])
    browser.close = AsyncMock()
    return browser


# ---------------------------------------------------------------------------
# open / close guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_browser_guard_blocks_action_when_not_open():
    tool = BrowserTool()
    result = await tool.execute(
        ToolInvocation(id="1", name="browser", params={"action": "snapshot"}),
    )
    assert result.is_error
    assert "open" in result.content.lower()


@pytest.mark.asyncio
async def test_browser_close_when_not_open_is_ok():
    tool = BrowserTool()
    result = await tool.execute(
        ToolInvocation(id="1", name="browser", params={"action": "close"}),
    )
    assert not result.is_error
    assert "not open" in result.content.lower()


@pytest.mark.asyncio
async def test_browser_guard_passes_after_open():
    """After open sets _browser, subsequent actions are allowed."""
    tool = BrowserTool()
    browser = _make_browser()
    # Manually inject an open browser (bypasses real CDP launch)
    tool._browser = browser

    result = await tool.execute(
        ToolInvocation(id="1", name="browser", params={"action": "snapshot"}),
    )
    assert not result.is_error
    assert "Example" in result.content


@pytest.mark.asyncio
async def test_browser_close_clears_state():
    tool = BrowserTool()
    tool._browser = _make_browser()

    result = await tool.execute(
        ToolInvocation(id="1", name="browser", params={"action": "close"}),
    )
    assert not result.is_error
    assert tool._browser is None

    # Next action must fail with the guard error
    blocked = await tool.execute(
        ToolInvocation(id="2", name="browser", params={"action": "snapshot"}),
    )
    assert blocked.is_error


# ---------------------------------------------------------------------------
# state_message — ephemeral injection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_state_message_returns_none_when_closed():
    tool = BrowserTool()
    msg = await tool.state_message()
    assert msg is None


@pytest.mark.asyncio
async def test_state_message_returns_browser_state_when_open():
    tool = BrowserTool()
    tool._browser = _make_browser()

    msg = await tool.state_message()
    assert msg is not None
    assert "[Current browser state]" in msg.contents[0].content
    assert "Example" in msg.contents[0].content


@pytest.mark.asyncio
async def test_state_message_suppresses_exceptions():
    tool = BrowserTool()
    browser = MagicMock()
    browser.get_state = AsyncMock(side_effect=RuntimeError("CDP gone"))
    tool._browser = browser

    msg = await tool.state_message()
    assert msg is None


# ---------------------------------------------------------------------------
# Screenshot origin — PIL Image vs bytes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_browser_state_screenshot_is_pil_image_by_default():
    """get_state() with no as_bytes → screenshot is a PIL Image."""
    img = _make_image()
    browser = _make_browser(screenshot=img)
    state = await browser.get_state()
    assert isinstance(state.screenshot, PILImage.Image)


@pytest.mark.asyncio
async def test_browser_state_screenshot_is_bytes_when_requested():
    """get_state(as_bytes=True) → screenshot is bytes."""
    raw = _image_to_bytes(_make_image())
    browser = _make_browser(screenshot=raw)
    state = await browser.get_state(as_bytes=True)
    assert isinstance(state.screenshot, bytes)


@pytest.mark.asyncio
async def test_browser_state_screenshot_none_when_no_vision():
    """get_state() without use_vision → screenshot stays None."""
    browser = _make_browser(screenshot=None)
    state = await browser.get_state()
    assert state.screenshot is None

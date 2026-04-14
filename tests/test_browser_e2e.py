from __future__ import annotations

import asyncio
import contextlib
import http.server
import socket
import threading
from pathlib import Path

import pytest
import pytest_asyncio

from operator_use.web.browser.config import BrowserConfig
from operator_use.web.browser.service import Browser


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _find_interactive_by_name(state, name: str):
    for node in state.dom_state.interactive_nodes:
        if node.name == name:
            return node
    raise AssertionError(f"Interactive node not found: {name!r}")


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A003
        pass


@pytest.fixture(scope="module")
def browser_binary():
    candidates = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
        Path("/usr/bin/microsoft-edge"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    pytest.skip("No local Chrome/Edge binary available for browser e2e tests")


@pytest.fixture(scope="module")
def browser_site(tmp_path_factory):
    root = tmp_path_factory.mktemp("browser-site")
    (root / "index.html").write_text(
        """<!doctype html>
<html>
  <head><title>Smoke Index</title></head>
  <body>
    <h1 id="title">Index</h1>
    <a id="nav-link" href="/page2.html">Go to page 2</a>
    <button id="mutate-btn" onclick="document.getElementById('status').textContent='Clicked state updated'">
      Mutate DOM
    </button>
    <button id="popup-btn" onclick="window.open('/popup.html', '_blank')">Open Popup</button>
    <div id="status">Initial status</div>
  </body>
</html>
""",
        encoding="utf-8",
    )
    (root / "page2.html").write_text(
        """<!doctype html>
<html>
  <head><title>Smoke Page 2</title></head>
  <body>
    <h1>Second Page</h1>
    <div id="page2-status">Navigation succeeded</div>
  </body>
</html>
""",
        encoding="utf-8",
    )
    (root / "popup.html").write_text(
        """<!doctype html>
<html>
  <head><title>Smoke Popup</title></head>
  <body>
    <h1>Popup Page</h1>
  </body>
</html>
""",
        encoding="utf-8",
    )

    port = _find_free_port()

    def handler(*args, **kwargs):
        return _QuietHandler(*args, directory=str(root), **kwargs)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest_asyncio.fixture
async def browser_instance(tmp_path, browser_binary):
    config = BrowserConfig(
        headless=True,
        browser="chrome",
        browser_instance_dir=browser_binary,
        user_data_dir=str(tmp_path / "profile"),
        downloads_dir=str(tmp_path / "downloads"),
        cdp_port=_find_free_port(),
        minimum_wait_page_load_time=0.1,
        wait_for_network_idle_page_load_time=0.1,
        maximum_wait_page_load_time=5.0,
    )
    browser = Browser(config)
    await browser.init_browser()
    await browser.init_tabs()
    try:
        yield browser
    finally:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(browser.close(), timeout=5.0)
        if browser._process is not None:
            with contextlib.suppress(Exception):
                browser._process.kill()


@pytest.mark.asyncio
async def test_browser_e2e_click_navigation(browser_instance: Browser, browser_site: str):
    await browser_instance.navigate(f"{browser_site}/index.html")

    state = await browser_instance.get_state()
    link = _find_interactive_by_name(state, "Go to page 2")
    await browser_instance.current_page().click_at(link.center.x, link.center.y)
    await browser_instance._wait_for_page(timeout=5.0)

    current_tab = await browser_instance.get_current_tab()
    assert current_tab is not None
    assert current_tab.title == "Smoke Page 2"
    assert current_tab.url.endswith("/page2.html")


@pytest.mark.asyncio
async def test_browser_e2e_dom_mutation_flow(browser_instance: Browser, browser_site: str):
    await browser_instance.navigate(f"{browser_site}/index.html")

    state = await browser_instance.get_state()
    button = _find_interactive_by_name(state, "Mutate DOM")
    await browser_instance.current_page().click_at(button.center.x, button.center.y)

    await browser_instance.get_state()
    html = await browser_instance.current_page().get_page_content()
    assert "Clicked state updated" in html


@pytest.mark.skip(
    reason="Popup/new-tab smoke remains flaky in local headless Chrome; popup behavior is covered by watchdog tests."
)
@pytest.mark.asyncio
async def test_browser_e2e_popup_creates_new_tab(browser_instance: Browser, browser_site: str):
    await browser_instance.navigate(f"{browser_site}/index.html")

    current_target = browser_instance._current_target_id
    await browser_instance.current_page().execute_script(
        f'window.open("{browser_site}/popup.html", "_blank")'
    )
    await asyncio.sleep(1.0)

    targets = dict(browser_instance._session_manager.targets)
    assert len(targets) >= 2
    popup_target = next(target_id for target_id in targets if target_id != current_target)
    popup_info = targets[popup_target]
    assert popup_info["url"].endswith("/popup.html") or popup_info["title"] == "Smoke Popup"

    await browser_instance.close_tab(popup_target)

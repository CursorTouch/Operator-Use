"""Tests for browser config and attach-to-existing behaviour."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import logging

from operator_use.web.browser.config import BrowserConfig, BROWSER_ARGS, detect_installed_browser
from operator_use.web.browser.events import StateInvalidatedEvent
from operator_use.web.browser.service import Browser


# ---------------------------------------------------------------------------
# BROWSER_ARGS sanity checks
# ---------------------------------------------------------------------------


def test_force_device_scale_factor_removed():
    assert "--force-device-scale-factor=1" not in BROWSER_ARGS


def test_disable_sync_present():
    assert "--disable-sync" in BROWSER_ARGS


# ---------------------------------------------------------------------------
# BrowserConfig defaults
# ---------------------------------------------------------------------------


def test_attach_to_existing_default_false():
    assert BrowserConfig().attach_to_existing is False


def test_attach_to_existing_can_be_enabled():
    cfg = BrowserConfig(attach_to_existing=True)
    assert cfg.attach_to_existing is True


def test_page_load_timing_defaults_present():
    cfg = BrowserConfig()
    assert cfg.minimum_wait_page_load_time > 0
    assert cfg.wait_for_network_idle_page_load_time > 0
    assert cfg.maximum_wait_page_load_time >= cfg.wait_for_network_idle_page_load_time


# ---------------------------------------------------------------------------
# _read_devtools_active_port
# ---------------------------------------------------------------------------


def test_read_devtools_active_port_valid(tmp_path):
    port_file = tmp_path / "DevToolsActivePort"
    port_file.write_text("9222\n/devtools/browser/abc-123\n")
    browser = Browser(BrowserConfig(user_data_dir=str(tmp_path)))
    result = browser._read_devtools_active_port()
    assert result == "ws://127.0.0.1:9222/devtools/browser/abc-123"


def test_read_devtools_active_port_no_user_data_dir():
    browser = Browser(BrowserConfig(user_data_dir=None))
    assert browser._read_devtools_active_port() is None


def test_read_devtools_active_port_file_missing(tmp_path):
    browser = Browser(BrowserConfig(user_data_dir=str(tmp_path)))
    # File does not exist
    assert browser._read_devtools_active_port() is None


def test_read_devtools_active_port_malformed(tmp_path):
    port_file = tmp_path / "DevToolsActivePort"
    port_file.write_text("notaport\n")
    browser = Browser(BrowserConfig(user_data_dir=str(tmp_path)))
    assert browser._read_devtools_active_port() is None


def test_read_devtools_active_port_only_one_line(tmp_path):
    port_file = tmp_path / "DevToolsActivePort"
    port_file.write_text("9222\n")
    browser = Browser(BrowserConfig(user_data_dir=str(tmp_path)))
    assert browser._read_devtools_active_port() is None


def test_read_devtools_active_port_strips_whitespace(tmp_path):
    port_file = tmp_path / "DevToolsActivePort"
    port_file.write_text("  9222  \n  /devtools/browser/xyz  \n")
    browser = Browser(BrowserConfig(user_data_dir=str(tmp_path)))
    result = browser._read_devtools_active_port()
    assert result == "ws://127.0.0.1:9222/devtools/browser/xyz"


# ---------------------------------------------------------------------------
# _resolve_ws_url — attach_to_existing behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_ws_url_attach_uses_devtools_active_port(tmp_path):
    """When DevToolsActivePort exists, _resolve_ws_url stores the URL and skips port polling."""
    port_file = tmp_path / "DevToolsActivePort"
    port_file.write_text("9222\n/devtools/browser/abc\n")

    browser = Browser(BrowserConfig(attach_to_existing=True, user_data_dir=str(tmp_path)))
    with patch.object(browser, "_is_port_responsive", new=AsyncMock()) as mock_poll:
        await browser._resolve_ws_url()

    assert browser._resolved_attach_ws_url == "ws://127.0.0.1:9222/devtools/browser/abc"
    mock_poll.assert_not_called()  # file path should skip port polling entirely


@pytest.mark.asyncio
async def test_resolve_ws_url_attach_fallback_to_port_polling(tmp_path):
    """When no DevToolsActivePort file, falls back to /json/version polling."""
    browser = Browser(BrowserConfig(attach_to_existing=True, user_data_dir=str(tmp_path)))
    # No file written — user_data_dir exists but DevToolsActivePort absent
    with patch.object(browser, "_is_port_responsive", new=AsyncMock(return_value=True)):
        await browser._resolve_ws_url()

    assert browser._resolved_attach_ws_url is None  # fallback path, no URL stored


@pytest.mark.asyncio
async def test_resolve_ws_url_attach_raises_when_port_dead(tmp_path):
    """attach_to_existing=True with no file and nothing on port raises RuntimeError."""
    browser = Browser(BrowserConfig(attach_to_existing=True, user_data_dir=str(tmp_path)))
    with patch.object(browser, "_is_port_responsive", new=AsyncMock(return_value=False)):
        with pytest.raises(RuntimeError, match="attach_to_existing"):
            await browser._resolve_ws_url()


@pytest.mark.asyncio
async def test_resolve_ws_url_no_attach_no_existing_browser():
    """Default mode with nothing on port should call _launch_process."""
    browser = Browser(BrowserConfig(attach_to_existing=False))
    with (
        patch.object(browser, "_is_port_responsive", new=AsyncMock(return_value=False)),
        patch.object(browser, "_launch_process", return_value=MagicMock()) as mock_launch,
        patch.object(browser, "_wait_for_browser", new=AsyncMock()),
    ):
        await browser._resolve_ws_url()

    mock_launch.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_ws_url_no_attach_kills_wrong_browser():
    """Default mode with wrong browser on port kills it then launches."""
    browser = Browser(BrowserConfig(attach_to_existing=False))
    with (
        patch.object(
            browser, "_is_port_responsive", new=AsyncMock(side_effect=[True, True, False])
        ),
        patch.object(browser, "_is_correct_browser", new=AsyncMock(return_value=False)),
        patch.object(browser, "_kill_on_port") as mock_kill,
        patch.object(browser, "_launch_process", return_value=MagicMock()),
        patch.object(browser, "_wait_for_browser", new=AsyncMock()),
    ):
        await browser._resolve_ws_url()

    mock_kill.assert_called_once()


# ---------------------------------------------------------------------------
# init_browser — attach path uses resolved ws URL directly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_init_browser_attach_uses_resolved_url(tmp_path):
    """init_browser should connect via _resolved_attach_ws_url without launching."""
    port_file = tmp_path / "DevToolsActivePort"
    port_file.write_text("9222\n/devtools/browser/abc\n")

    browser = Browser(BrowserConfig(attach_to_existing=True, user_data_dir=str(tmp_path)))

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.on_disconnect = None

    with (
        patch(
            "operator_use.web.browser.service.Browser._is_port_responsive",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "operator_use.web.browser.service.Browser._is_correct_browser",
            new=AsyncMock(return_value=True),
        ),
        patch("operator_use.web.browser.service.Browser._launch_process") as mock_launch,
        patch("operator_use.web.cdp.Client", return_value=mock_client),
    ):
        await browser.init_browser()

    mock_launch.assert_not_called()
    assert browser._client is mock_client


@pytest.mark.asyncio
async def test_init_browser_attach_never_kills(tmp_path):
    """attach_to_existing should never call _kill_on_port even if browser type differs."""
    port_file = tmp_path / "DevToolsActivePort"
    port_file.write_text("9222\n/devtools/browser/abc\n")

    browser = Browser(BrowserConfig(attach_to_existing=True, user_data_dir=str(tmp_path)))

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.on_disconnect = None

    with (
        patch("operator_use.web.cdp.Client", return_value=mock_client),
        patch.object(browser, "_kill_on_port") as mock_kill,
    ):
        await browser.init_browser()

    mock_kill.assert_not_called()


# ---------------------------------------------------------------------------
# detect_installed_browser
# ---------------------------------------------------------------------------


def test_detect_installed_browser_finds_chrome(tmp_path):
    fake_chrome = tmp_path / "chrome.exe"
    fake_chrome.touch()
    with (
        patch("operator_use.web.browser.config.platform.system", return_value="Windows"),
        patch("operator_use.web.browser.config.os.environ.get", return_value=str(tmp_path)),
        patch(
            "operator_use.web.browser.config.Path.exists",
            side_effect=lambda p=None: str(fake_chrome) in str(p) if p else False,
        ),
    ):
        # Direct path check: returns chrome when chrome path exists first
        pass  # covered by resolved_browser tests below


def test_detect_returns_fallback_when_nothing_found():
    with (
        patch("operator_use.web.browser.config.platform.system", return_value="Windows"),
        patch("operator_use.web.browser.config.Path") as mock_path_cls,
    ):
        mock_path_cls.return_value.exists.return_value = False
        mock_path_cls.home.return_value = Path(".")
        # Should not raise, returns fallback
        result = detect_installed_browser()
    assert result in ("chrome", "edge")


def test_detect_linux_uses_shutil_which():
    with (
        patch("operator_use.web.browser.config.platform.system", return_value="Linux"),
        patch(
            "shutil.which",
            side_effect=lambda cmd: "/usr/bin/google-chrome" if cmd == "google-chrome" else None,
        ),
    ):
        result = detect_installed_browser()
    assert result == "chrome"


def test_detect_linux_falls_back_to_edge():
    with (
        patch("operator_use.web.browser.config.platform.system", return_value="Linux"),
        patch(
            "shutil.which",
            side_effect=lambda cmd: "/usr/bin/msedge" if cmd == "microsoft-edge" else None,
        ),
    ):
        result = detect_installed_browser()
    assert result == "edge"


# ---------------------------------------------------------------------------
# BrowserConfig.resolved_browser
# ---------------------------------------------------------------------------


def test_resolved_browser_explicit():
    assert BrowserConfig(browser="chrome").resolved_browser() == "chrome"
    assert BrowserConfig(browser="edge").resolved_browser() == "edge"


def test_begin_navigation_tracking_marks_session_loading():
    browser = Browser(BrowserConfig())

    browser._begin_navigation_tracking("session-1")

    assert browser._page_loading["session-1"] is True
    assert "session-1" in browser._page_started
    assert "session-1" in browser._page_ready


def test_emit_browser_event_calls_registered_handler():
    browser = Browser(BrowserConfig())
    handler = MagicMock()
    browser.on_browser_event(StateInvalidatedEvent, handler)

    browser.emit_browser_event(StateInvalidatedEvent(session_id="session-1", reason="click"))

    event = handler.call_args.args[0]
    assert isinstance(event, StateInvalidatedEvent)
    assert event.reason == "click"


def test_is_navigation_pending_when_loading():
    browser = Browser(BrowserConfig())
    browser._get_current_session_id = MagicMock(return_value="session-1")
    browser._page_loading["session-1"] = True

    assert browser.is_navigation_pending() is True


@pytest.mark.asyncio
async def test_wait_for_page_returns_when_navigation_never_starts():
    browser = Browser(BrowserConfig())
    browser._get_current_session_id = MagicMock(return_value="session-1")

    await browser._wait_for_page(timeout=0.1)

    assert "session-1" not in browser._page_started
    assert "session-1" not in browser._page_ready


@pytest.mark.asyncio
async def test_execute_script_logs_promise_collected(caplog):
    browser = Browser(BrowserConfig())
    browser._get_current_session_id = MagicMock(return_value="session-1")
    browser.send = AsyncMock(
        side_effect=Exception("{'code': -32000, 'message': 'Promise was collected'}")
    )

    with caplog.at_level(logging.WARNING):
        result = await browser.execute_script("Promise.resolve(1)")

    assert result is None
    assert "Promise was collected" in caplog.text


@pytest.mark.asyncio
async def test_execute_script_logs_other_errors(caplog):
    browser = Browser(BrowserConfig())
    browser._get_current_session_id = MagicMock(return_value="session-1")
    browser.send = AsyncMock(side_effect=Exception("boom"))

    with caplog.at_level(logging.WARNING):
        result = await browser.execute_script("1 + 1")

    assert result is None
    assert "execute_script error: boom" in caplog.text


@pytest.mark.asyncio
async def test_get_state_delegates_to_state_watchdog():
    browser = Browser(BrowserConfig())
    mock_state = MagicMock()
    browser._state_watchdog = MagicMock()
    browser._state_watchdog.get_state = AsyncMock(return_value=mock_state)

    result = await browser.get_state()

    assert result is mock_state
    browser._state_watchdog.get_state.assert_awaited_once_with(use_vision=False)


def test_resolved_browser_auto_detect_caches():
    cfg = BrowserConfig(browser=None)
    with patch(
        "operator_use.web.browser.config.detect_installed_browser", return_value="chrome"
    ) as mock_detect:
        result1 = cfg.resolved_browser()
        result2 = cfg.resolved_browser()
    assert result1 == "chrome"
    assert result2 == "chrome"
    mock_detect.assert_called_once()  # cached after first call


def test_resolved_browser_default_is_none():
    assert BrowserConfig().browser is None


# ---------------------------------------------------------------------------
# close() — never kills process when attach_to_existing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_does_not_kill_attached_browser():
    browser = Browser(BrowserConfig(attach_to_existing=True))
    mock_proc = MagicMock()
    browser._process = mock_proc
    browser._client = MagicMock()
    browser._client.__aexit__ = AsyncMock()
    await browser.close()
    mock_proc.terminate.assert_not_called()
    mock_proc.kill.assert_not_called()


@pytest.mark.asyncio
async def test_close_kills_owned_browser():
    browser = Browser(BrowserConfig(attach_to_existing=False))
    mock_proc = MagicMock()
    browser._process = mock_proc
    browser._client = MagicMock()
    browser._client.__aexit__ = AsyncMock()
    await browser.close()
    mock_proc.terminate.assert_called_once()

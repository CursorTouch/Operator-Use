"""Tests for the control_center tool — audit logging, plugin toggles, restart."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from operator_use.tools.control_center import (
    control_center,
    _set_plugin_enabled,
    _get_plugin_enabled,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(agent_id: str = "op", plugins: list | None = None) -> dict:
    return {"agents": {"list": [{"id": agent_id, "plugins": plugins or []}]}}


def _call_cc(**kwargs):
    """Invoke control_center's underlying function directly (bypasses Tool wrapper)."""
    return control_center.function(
        computer_use=kwargs.pop("computer_use", None),
        browser_use=kwargs.pop("browser_use", None),
        restart=kwargs.pop("restart", False),
        continue_with=kwargs.pop("continue_with", None),
        agent_id=kwargs.pop("agent_id", None),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# _set_plugin_enabled / _get_plugin_enabled helpers
# ---------------------------------------------------------------------------


def test_set_plugin_adds_entry_when_absent():
    entry = {"id": "op", "plugins": []}
    _set_plugin_enabled(entry, "browser_use", True)
    assert entry["plugins"] == [{"id": "browser_use", "enabled": True}]


def test_set_plugin_updates_existing_entry():
    entry = {"id": "op", "plugins": [{"id": "browser_use", "enabled": True}]}
    _set_plugin_enabled(entry, "browser_use", False)
    assert entry["plugins"][0]["enabled"] is False


def test_get_plugin_returns_false_when_absent():
    entry = {"id": "op", "plugins": []}
    assert _get_plugin_enabled(entry, "browser_use") is False


def test_get_plugin_returns_correct_value():
    entry = {"id": "op", "plugins": [{"id": "computer_use", "enabled": True}]}
    assert _get_plugin_enabled(entry, "computer_use") is True


# ---------------------------------------------------------------------------
# control_center — plugin toggles call agent methods
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enable_browser_use_calls_agent(tmp_path):
    cfg = _make_config()
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))

    mock_agent = MagicMock()
    mock_agent.enable_browser_use = AsyncMock()

    with patch("operator_use.agent.tools.builtin.control_center.CONFIG_PATH", cfg_file):
        result = await _call_cc(browser_use=True, _agent=mock_agent)

    mock_agent.enable_browser_use.assert_awaited_once()
    assert result.success


@pytest.mark.asyncio
async def test_enable_both_computer_use_and_browser_use_independently(tmp_path):
    cfg = _make_config()
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))

    mock_agent = MagicMock()
    mock_agent.enable_computer_use = AsyncMock()
    mock_agent.enable_browser_use = AsyncMock()

    with patch("operator_use.agent.tools.builtin.control_center.CONFIG_PATH", cfg_file):
        result = await _call_cc(computer_use=True, browser_use=True, _agent=mock_agent)

    saved = json.loads(cfg_file.read_text())
    plugins = saved["agents"]["list"][0]["plugins"]
    cu = next(p for p in plugins if p["id"] == "computer_use")
    bu = next(p for p in plugins if p["id"] == "browser_use")
    assert cu["enabled"] is True
    assert bu["enabled"] is True
    assert result.success


@pytest.mark.asyncio
async def test_disable_browser_use_calls_agent(tmp_path):
    cfg = _make_config(plugins=[{"id": "browser_use", "enabled": True}])
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))

    mock_agent = MagicMock()
    mock_agent.disable_browser_use = AsyncMock()

    with patch("operator_use.agent.tools.builtin.control_center.CONFIG_PATH", cfg_file):
        result = await _call_cc(browser_use=False, _agent=mock_agent)

    mock_agent.disable_browser_use.assert_awaited_once()
    assert result.success


@pytest.mark.asyncio
async def test_status_only_returns_current_state(tmp_path):
    cfg = _make_config(plugins=[{"id": "browser_use", "enabled": True}])
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))

    with patch("operator_use.agent.tools.builtin.control_center.CONFIG_PATH", cfg_file):
        result = await _call_cc()

    assert result.success
    assert "browser_use" in result.output
    assert "computer_use" in result.output


# ---------------------------------------------------------------------------
# control_center — audit logging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_log_emitted_on_plugin_change(tmp_path, caplog):
    import logging

    cfg = _make_config()
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))

    mock_agent = MagicMock()
    mock_agent.enable_browser_use = AsyncMock()

    with patch("operator_use.agent.tools.builtin.control_center.CONFIG_PATH", cfg_file):
        with caplog.at_level(
            logging.WARNING, logger="operator_use.agent.tools.builtin.control_center"
        ):
            await _call_cc(
                browser_use=True,
                _agent=mock_agent,
                _channel="telegram",
                _chat_id="12345",
                _agent_id="op",
            )

    assert any("browser_use=true" in r.message for r in caplog.records)
    assert any("telegram" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_audit_log_emitted_on_status_check(tmp_path, caplog):
    import logging

    cfg = _make_config()
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))

    with patch("operator_use.agent.tools.builtin.control_center.CONFIG_PATH", cfg_file):
        with caplog.at_level(
            logging.WARNING, logger="operator_use.agent.tools.builtin.control_center"
        ):
            await _call_cc(_channel="discord", _chat_id="999", _agent_id="op")

    assert any("control_center" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# control_center — graceful restart wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_calls_graceful_fn_not_os_exit(tmp_path):
    cfg = _make_config()
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))

    graceful_called = []

    async def mock_graceful():
        graceful_called.append(True)

    with patch("operator_use.agent.tools.builtin.control_center.CONFIG_PATH", cfg_file):
        with patch("operator_use.agent.tools.builtin.control_center._do_restart") as mock_restart:
            mock_restart.return_value = None
            result = await _call_cc(restart=True, _graceful_restart_fn=mock_graceful)

    assert result.success
    assert "Restart initiated" in result.output
    mock_restart.assert_called_once()
    # Verify graceful_fn was passed through
    _, kwargs = mock_restart.call_args
    assert kwargs.get("graceful_fn") is mock_graceful


@pytest.mark.asyncio
async def test_restart_without_graceful_fn_still_works(tmp_path):
    """When _graceful_restart_fn is not wired, restart still returns success."""
    cfg = _make_config()
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg))

    with patch("operator_use.agent.tools.builtin.control_center.CONFIG_PATH", cfg_file):
        with patch("operator_use.agent.tools.builtin.control_center._do_restart"):
            result = await _call_cc(restart=True)

    assert result.success


# ---------------------------------------------------------------------------
# error cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_error_when_no_agents(tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"agents": {"list": []}}))

    with patch("operator_use.agent.tools.builtin.control_center.CONFIG_PATH", cfg_file):
        result = await _call_cc(browser_use=True)

    assert not result.success


@pytest.mark.asyncio
async def test_returns_error_when_config_missing(tmp_path):
    missing = tmp_path / "no_config.json"

    with patch("operator_use.agent.tools.builtin.control_center.CONFIG_PATH", missing):
        result = await _call_cc(browser_use=True)

    assert not result.success

"""Tests for the control_center tool — audit logging and restart."""

import pytest
from unittest.mock import patch

from operator_use.tools.control_center import control_center


def _call_cc(**kwargs):
    """Invoke control_center's underlying function directly (bypasses Tool wrapper)."""
    return control_center.function(
        restart=kwargs.pop("restart", False),
        continue_with=kwargs.pop("continue_with", None),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# control_center — no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_args_returns_no_action():
    result = await _call_cc()
    assert result.success
    assert "No action" in result.output


# ---------------------------------------------------------------------------
# control_center — audit logging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_log_emitted_on_restart(caplog):
    import logging

    with patch("operator_use.tools.control_center._do_restart"):
        with caplog.at_level(logging.WARNING, logger="operator_use.tools.control_center"):
            await _call_cc(restart=True, _channel="telegram", _chat_id="12345", _agent_id="op")

    assert any("telegram" in r.message for r in caplog.records)
    assert any("restart=True" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# control_center — graceful restart wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_calls_graceful_fn_not_os_exit():
    async def mock_graceful():
        pass

    with patch("operator_use.tools.control_center._do_restart") as mock_restart:
        mock_restart.return_value = None
        result = await _call_cc(restart=True, _graceful_restart_fn=mock_graceful)

    assert result.success
    assert "Restart initiated" in result.output
    mock_restart.assert_called_once()
    _, kwargs = mock_restart.call_args
    assert kwargs.get("graceful_fn") is mock_graceful


@pytest.mark.asyncio
async def test_restart_without_graceful_fn_still_works():
    with patch("operator_use.tools.control_center._do_restart"):
        result = await _call_cc(restart=True)
    assert result.success


@pytest.mark.asyncio
async def test_restart_with_continue_with_writes_restart_file(tmp_path):
    restart_file = tmp_path / "restart.json"

    with patch("operator_use.tools.control_center.RESTART_FILE", restart_file):
        with patch("operator_use.tools.control_center._do_restart"):
            result = await _call_cc(
                restart=True,
                continue_with="Test the new tool",
                _channel="telegram",
                _chat_id="123",
            )

    assert result.success
    assert restart_file.exists()
    import json
    data = json.loads(restart_file.read_text())
    assert data["resume_task"] == "Test the new tool"

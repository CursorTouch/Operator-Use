"""Example security test that validates the scaffold itself.

Serves as the acceptance-criteria "at least one example security test
passes using the scaffold" required by issue #7.  It also acts as
living documentation showing how Phase 1 security tests should be
structured.
"""

from pathlib import Path
from typing import NamedTuple

import pytest

from tests.security.helpers import (
    assert_allowed,
    assert_blocked,
    command_injection_payloads,
    path_traversal_payloads,
)


# ---------------------------------------------------------------------------
# Minimal stub that mimics the ToolResult interface
# ---------------------------------------------------------------------------


class _StubResult(NamedTuple):
    success: bool
    output: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Fixture smoke tests
# ---------------------------------------------------------------------------


def test_tmp_workspace_is_isolated_directory(tmp_workspace: Path) -> None:
    """tmp_workspace provides a fresh, writable directory per test."""
    assert tmp_workspace.exists()
    assert tmp_workspace.is_dir()
    sentinel = tmp_workspace / "sentinel.txt"
    sentinel.write_text("ok")
    assert sentinel.read_text() == "ok"


def test_tmp_workspace_contains_expected_subdirs(tmp_workspace: Path) -> None:
    """tmp_workspace pre-creates files/ and logs/ subdirectories."""
    assert (tmp_workspace / "files").is_dir()
    assert (tmp_workspace / "logs").is_dir()


def test_mock_agent_context_keys(mock_agent_context: dict) -> None:
    """mock_agent_context contains required keys for tool testing."""
    required_keys = {"workspace", "agent_id", "permissions"}
    assert required_keys.issubset(mock_agent_context.keys())


def test_mock_agent_context_workspace_is_path(mock_agent_context: dict) -> None:
    """mock_agent_context workspace value is a Path pointing to tmp_workspace."""
    assert isinstance(mock_agent_context["workspace"], Path)
    assert mock_agent_context["workspace"].exists()


def test_capture_logs_captures_debug_messages(capture_logs) -> None:
    """capture_logs fixture intercepts log records at DEBUG level."""
    import logging

    logger = logging.getLogger("operator_use.security.test")
    logger.debug("scaffold-debug-sentinel")
    assert any("scaffold-debug-sentinel" in r.message for r in capture_logs.records)


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


def test_path_traversal_payloads_returns_nonempty_list() -> None:
    """path_traversal_payloads() returns at least one payload."""
    payloads = path_traversal_payloads()
    assert len(payloads) > 0
    assert all(isinstance(p, str) for p in payloads)


def test_path_traversal_payloads_contain_dotdot() -> None:
    """path_traversal_payloads() includes classic ../ traversals."""
    payloads = path_traversal_payloads()
    assert any(".." in p for p in payloads)


def test_command_injection_payloads_returns_nonempty_list() -> None:
    """command_injection_payloads() returns at least one payload."""
    payloads = command_injection_payloads()
    assert len(payloads) > 0
    assert all(isinstance(p, str) for p in payloads)


def test_command_injection_payloads_contain_shell_operators() -> None:
    """command_injection_payloads() includes common shell operator chars."""
    payloads = command_injection_payloads()
    shell_chars = set(";|&`$")
    assert any(shell_chars & set(p) for p in payloads)


# ---------------------------------------------------------------------------
# assert_blocked / assert_allowed helper tests
# ---------------------------------------------------------------------------


def test_assert_blocked_passes_on_failure_result() -> None:
    """assert_blocked does not raise when result.success is False."""
    assert_blocked(_StubResult(success=False, error="denied"))


def test_assert_blocked_raises_on_success_result() -> None:
    """assert_blocked raises AssertionError when result.success is True."""
    with pytest.raises(AssertionError, match="blocked"):
        assert_blocked(_StubResult(success=True, output="ok"))


def test_assert_blocked_raises_when_extra_check_fails() -> None:
    """assert_blocked raises when the optional check predicate returns False."""
    with pytest.raises(AssertionError, match="Extra check failed"):
        assert_blocked(_StubResult(success=False, error="denied"), check=lambda _: False)


def test_assert_allowed_passes_on_success_result() -> None:
    """assert_allowed does not raise when result.success is True."""
    assert_allowed(_StubResult(success=True, output="ok"))


def test_assert_allowed_raises_on_failure_result() -> None:
    """assert_allowed raises AssertionError when result.success is False."""
    with pytest.raises(AssertionError, match="allowed"):
        assert_allowed(_StubResult(success=False, error="denied"))


def test_assert_allowed_raises_when_extra_check_fails() -> None:
    """assert_allowed raises when the optional check predicate returns False."""
    with pytest.raises(AssertionError, match="Extra check failed"):
        assert_allowed(_StubResult(success=True, output="ok"), check=lambda _: False)

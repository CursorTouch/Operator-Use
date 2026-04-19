"""Shared fixtures for the security test suite."""

import logging
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Generator[Path, None, None]:
    """Isolated temporary directory simulating an agent workspace.

    Creates a directory tree that mirrors the structure an agent would
    operate in: a workspace root with ``files/`` and ``logs/`` subdirs.
    The fixture yields the workspace root and guarantees cleanup after
    the test, even on failure.

    Yields:
        Path: The root of the isolated workspace.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "files").mkdir()
    (workspace / "logs").mkdir()
    yield workspace
    # tmp_path cleanup is handled by pytest automatically


@pytest.fixture
def mock_agent_context(tmp_workspace: Path) -> dict:
    """Minimal agent context dictionary for tool testing.

    Provides the smallest set of keys required by tools under test so
    that individual security tests do not need to construct full agent
    objects.

    Args:
        tmp_workspace: The isolated workspace fixture.

    Returns:
        dict: A context mapping with ``workspace``, ``agent_id``, and
        ``permissions`` keys.
    """
    return {
        "workspace": tmp_workspace,
        "agent_id": "test-agent-001",
        "permissions": {
            "read": True,
            "write": True,
            "execute": False,
            "network": False,
        },
    }


@pytest.fixture
def capture_logs(
    caplog: pytest.LogCaptureFixture,
) -> Generator[pytest.LogCaptureFixture, None, None]:
    """Fixture to capture and inspect log output during a test.

    Sets the root logger to DEBUG level for the duration of the test so
    that security-related log lines emitted at any level are visible.

    Yields:
        pytest.LogCaptureFixture: The caplog object; use
        ``capture_logs.records`` or ``capture_logs.text`` to inspect
        what was logged.

    Example::

        def test_blocked_action_is_logged(capture_logs):
            trigger_blocked_action()
            assert any("blocked" in r.message.lower() for r in capture_logs.records)
    """
    with caplog.at_level(logging.DEBUG):
        yield caplog

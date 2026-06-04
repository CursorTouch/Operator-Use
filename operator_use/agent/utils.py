from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorKind(str, Enum):
    """Why an LLM API call failed — determines recovery strategy."""
    # Auth
    AUTH             = "auth"          # 401/403, transient (expired key) — abort, no rotation
    AUTH_PERMANENT   = "auth_permanent" # auth failed with no path to recovery — abort
    # Quota / billing
    BILLING          = "billing"       # 402, credits exhausted — abort immediately
    RATE_LIMIT       = "rate_limit"    # 429, throttle — backoff + retry
    # Server-side
    OVERLOADED       = "overloaded"    # 503/529, provider busy — backoff + retry
    SERVER_ERROR     = "server_error"  # 500/502, internal — retry
    # Transport
    TIMEOUT          = "timeout"       # connection/read timeout — retry
    # Context / payload
    CONTEXT_OVERFLOW = "context_overflow"  # context too large — compact, then retry
    # Model / policy
    MODEL_NOT_FOUND  = "model_not_found"    # 404, invalid model — abort
    CONTENT_BLOCKED  = "content_blocked"    # safety filter — abort, don't retry
    FORMAT_ERROR     = "format_error"       # 400 bad request — abort
    # Catch-all
    UNKNOWN          = "unknown"       # unclassified — retry with backoff


@dataclass
class ClassifiedError:
    """Structured classification of an API error with recovery hints."""
    kind: ErrorKind
    message: str = ""
    status_code: int | None = None
    # Recovery hints — checked by _run_with_retry instead of re-classifying
    retryable: bool = True
    should_compact: bool = False  # context_overflow → run compaction before retry


# ── Pattern lists ──────────────────────────────────────────────────────────────

_BILLING_PATTERNS = (
    "insufficient credits", "insufficient_quota", "insufficient balance",
    "credits exhausted", "no usable credits", "top up your credits",
    "payment required", "billing hard limit", "exceeded your current quota",
    "account is deactivated", "out of funds", "run out of funds",
    "balance_depleted", "not available on the free tier",
)

_RATE_LIMIT_PATTERNS = (
    "rate limit", "rate_limit", "too many requests", "throttled",
    "requests per minute", "tokens per minute", "requests per day",
    "try again in", "please retry after", "resource_exhausted",
    "throttlingexception", "too many concurrent requests",
)

_CONTEXT_OVERFLOW_PATTERNS = (
    "context length", "context size", "maximum context", "token limit",
    "too many tokens", "reduce the length", "exceeds the limit",
    "context window", "prompt is too long", "prompt exceeds max length",
    "maximum number of tokens", "exceeds the max_model_len", "max_model_len",
    "input is too long", "maximum model length", "context length exceeded",
    "slot context", "n_ctx_slot", "超过最大长度", "上下文长度",
    "max input token", "exceeds the maximum number of input tokens",
)

_MODEL_NOT_FOUND_PATTERNS = (
    "is not a valid model", "invalid model", "model not found",
    "model_not_found", "does not exist", "no such model",
    "unknown model", "unsupported model",
)

_CONTENT_BLOCKED_PATTERNS = (
    "flagged for possible cybersecurity risk",
    "violates our usage policies", "violates openai's usage policies",
    "your request was flagged by",
    "prompt was flagged by our safety",
    "responses cannot be generated due to safety",
    "content_filter", "responsibleaipolicyviolation",
)

_AUTH_PATTERNS = (
    "invalid api key", "invalid_api_key", "authentication",
    "unauthorized", "forbidden", "invalid token",
    "token expired", "token revoked", "access denied",
    "no api key", "api key not", "permission denied",
)

_FORMAT_ERROR_PATTERNS = (
    "invalid request", "invalid_request_error", "bad request",
    "badrequesterror", "bad_request", "unknown parameter",
    "unsupported parameter", "unrecognized request argument",
)

_TIMEOUT_PATTERNS = (
    "timed out", "turn timed out", "request timed out",
    "deadline exceeded", "operation timed out", "upstream timed out",
)

_TRANSPORT_ERROR_TYPES = frozenset({
    "ReadTimeout", "ConnectTimeout", "PoolTimeout",
    "ConnectError", "RemoteProtocolError", "ConnectionError",
    "ConnectionResetError", "ConnectionAbortedError", "BrokenPipeError",
    "TimeoutError", "ReadError", "ServerDisconnectedError",
    "APIConnectionError", "APITimeoutError",
})


# ── Helpers ────────────────────────────────────────────────────────────────────

def _status(error: Exception) -> int | None:
    for attr in ("status_code", "status", "code", "http_status"):
        val = getattr(error, attr, None)
        if isinstance(val, int):
            return val
    return None


def _msg(error: Exception) -> str:
    try:
        body = getattr(error, "body", None) or getattr(error, "response", None)
        if isinstance(body, dict):
            err = body.get("error", {})
            if isinstance(err, dict):
                return (str(err.get("message") or "") + " " + str(body)).lower()
        if body:
            return (str(error) + " " + str(body)).lower()
    except Exception:
        pass
    return str(error).lower()


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(p in text for p in patterns)


# ── Classifier ─────────────────────────────────────────────────────────────────

def classify_error(error: Exception) -> ClassifiedError:
    """Classify an LLM API error into a structured recovery recommendation."""
    status = _status(error)
    msg = _msg(error)
    error_type = type(error).__name__

    # Force 429 when SDK exposes RateLimitError without a status code
    if status is None and error_type == "RateLimitError":
        status = 429

    # ── 1. Content policy — deterministic, never retry unchanged ──────────────
    if _matches(msg, _CONTENT_BLOCKED_PATTERNS):
        return ClassifiedError(kind=ErrorKind.CONTENT_BLOCKED, message=msg, status_code=status, retryable=False)

    # ── 2. HTTP status-based classification ───────────────────────────────────
    if status == 401 or status == 403:
        if _matches(msg, ("invalid api key", "invalid_api_key", "incorrect api key", "no api key")):
            return ClassifiedError(kind=ErrorKind.AUTH_PERMANENT, message=msg, status_code=status, retryable=False)
        return ClassifiedError(kind=ErrorKind.AUTH, message=msg, status_code=status, retryable=False)

    if status == 402:
        return ClassifiedError(kind=ErrorKind.BILLING, message=msg, status_code=status, retryable=False)

    if status == 429:
        # Disambiguate billing exhaustion from rate limit — both are 429
        if _matches(msg, _BILLING_PATTERNS):
            return ClassifiedError(kind=ErrorKind.BILLING, message=msg, status_code=status, retryable=False)
        return ClassifiedError(kind=ErrorKind.RATE_LIMIT, message=msg, status_code=status, retryable=True)

    if status == 413:
        return ClassifiedError(kind=ErrorKind.CONTEXT_OVERFLOW, message=msg, status_code=status, retryable=True, should_compact=True)

    if status in (400, 422):
        if _matches(msg, _CONTEXT_OVERFLOW_PATTERNS):
            return ClassifiedError(kind=ErrorKind.CONTEXT_OVERFLOW, message=msg, status_code=status, retryable=True, should_compact=True)
        if _matches(msg, _MODEL_NOT_FOUND_PATTERNS):
            return ClassifiedError(kind=ErrorKind.MODEL_NOT_FOUND, message=msg, status_code=status, retryable=False)
        return ClassifiedError(kind=ErrorKind.FORMAT_ERROR, message=msg, status_code=status, retryable=False)

    if status == 404:
        if _matches(msg, _MODEL_NOT_FOUND_PATTERNS):
            return ClassifiedError(kind=ErrorKind.MODEL_NOT_FOUND, message=msg, status_code=status, retryable=False)
        return ClassifiedError(kind=ErrorKind.FORMAT_ERROR, message=msg, status_code=status, retryable=False)

    if status in (500, 502):
        # Some gateways return 5xx for deterministic request-validation errors
        if _matches(msg, _FORMAT_ERROR_PATTERNS):
            return ClassifiedError(kind=ErrorKind.FORMAT_ERROR, message=msg, status_code=status, retryable=False)
        return ClassifiedError(kind=ErrorKind.SERVER_ERROR, message=msg, status_code=status, retryable=True)

    if status in (503, 529):
        return ClassifiedError(kind=ErrorKind.OVERLOADED, message=msg, status_code=status, retryable=True)

    # ── 3. Message-pattern classification (no reliable status code) ───────────
    if _matches(msg, _BILLING_PATTERNS):
        return ClassifiedError(kind=ErrorKind.BILLING, message=msg, status_code=status, retryable=False)

    if _matches(msg, _RATE_LIMIT_PATTERNS):
        return ClassifiedError(kind=ErrorKind.RATE_LIMIT, message=msg, status_code=status, retryable=True)

    if _matches(msg, _CONTEXT_OVERFLOW_PATTERNS):
        return ClassifiedError(kind=ErrorKind.CONTEXT_OVERFLOW, message=msg, status_code=status, retryable=True, should_compact=True)

    if _matches(msg, _MODEL_NOT_FOUND_PATTERNS):
        return ClassifiedError(kind=ErrorKind.MODEL_NOT_FOUND, message=msg, status_code=status, retryable=False)

    if _matches(msg, _AUTH_PATTERNS):
        return ClassifiedError(kind=ErrorKind.AUTH_PERMANENT, message=msg, status_code=status, retryable=False)

    if _matches(msg, _FORMAT_ERROR_PATTERNS):
        return ClassifiedError(kind=ErrorKind.FORMAT_ERROR, message=msg, status_code=status, retryable=False)

    if _matches(msg, _TIMEOUT_PATTERNS):
        return ClassifiedError(kind=ErrorKind.TIMEOUT, message=msg, status_code=status, retryable=True)

    # ── 4. Transport error type names ─────────────────────────────────────────
    if error_type in _TRANSPORT_ERROR_TYPES or isinstance(error, (OSError, TimeoutError)):
        return ClassifiedError(kind=ErrorKind.TIMEOUT, message=msg, status_code=status, retryable=True)

    # ── 5. Unknown — retry with backoff ───────────────────────────────────────
    return ClassifiedError(kind=ErrorKind.UNKNOWN, message=msg, status_code=status, retryable=True)


# Keep the old name for any callers that use it directly
def is_permanent_error(error: str | None) -> bool:
    """Deprecated — use classify_error(). Kept for backward compatibility."""
    if not error:
        return False
    try:
        result = classify_error(Exception(error))
        return not result.retryable
    except Exception:
        return False

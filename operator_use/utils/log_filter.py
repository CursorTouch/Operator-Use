"""Credential masking for log output -- prevents secrets leaking into logs."""

import logging
import re


# Patterns that match common credential formats in log strings.
# Order matters: more specific patterns should come before general ones.
_MASK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # JWT-like strings (three base64url segments separated by dots)
    (
        re.compile(r"eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+"),
        "***JWT_REDACTED***",
    ),
    # Bearer token header values
    (
        re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
        r"\1***REDACTED***",
    ),
    # Provider-specific credential patterns
    (re.compile(r"gsk_[A-Za-z0-9]{8,}", re.IGNORECASE), "gsk_***REDACTED***"),
    (re.compile(r"AIza[A-Za-z0-9\-_]{8,}"), "AIza***REDACTED***"),
    (re.compile(r"nvapi-[A-Za-z0-9\-_]{8,}", re.IGNORECASE), "nvapi-***REDACTED***"),
    # API keys / tokens with common prefixes (sk-, pk-, api-, token-, key-)
    # Allows multi-segment keys like sk-proj-abc12345678
    (
        re.compile(r"(sk|pk|api|token|key)[-_][A-Za-z0-9\-_]{8,}", re.IGNORECASE),
        r"\1-***REDACTED***",
    ),
    # Authorization / x-api-key / x-auth-token headers
    (
        re.compile(
            r"(authorization|x-api-key|x-auth-token)\s*[:=]\s*\S+", re.IGNORECASE
        ),
        r"\1: ***REDACTED***",
    ),
    # password= / secret= / token= / api_key= patterns in query strings or log lines
    (
        re.compile(
            r"(password|secret|passwd|pwd|token|api_key|apikey)\s*[=:]\s*\S+",
            re.IGNORECASE,
        ),
        r"\1=***REDACTED***",
    ),
    # Generic high-entropy secrets: key=value or key: value where value is 32+ alphanum chars
    (
        re.compile(r"(\b\w+\b\s*[=:]\s*)([A-Za-z0-9_\-]{32,})"),
        r"\1***REDACTED***",
    ),
]


def mask_credentials(text: str) -> str:
    """Apply all credential masking patterns to a string."""
    for pattern, replacement in _MASK_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class CredentialMaskingFilter(logging.Filter):
    """Logging filter that redacts credential patterns from all log records.

    Uses record.getMessage() to render the final formatted message before masking,
    then clears record.args so the formatter does not re-apply %-style substitution.
    This avoids TypeError when log args include numeric placeholders (%d, %.2f).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Render the message with its args first to preserve type semantics,
        # then mask the rendered string. Clear args so the handler formatter
        # does not re-format (which would re-expose the original values).
        rendered = record.getMessage()
        record.msg = mask_credentials(rendered)
        record.args = ()
        return True


def install_credential_masking() -> None:
    """Install credential masking on the root logger and all its handlers.

    Attaches CredentialMaskingFilter both to the root logger and to every
    handler on the root logger, ensuring records emitted via named loggers
    (logging.getLogger(__name__)) are masked regardless of propagation path.
    Call once at startup.
    """
    root_logger = logging.getLogger()
    filter_instance = CredentialMaskingFilter()

    # Add to root logger filters (catches records at the logger level)
    if not any(isinstance(f, CredentialMaskingFilter) for f in root_logger.filters):
        root_logger.addFilter(filter_instance)

    # Also add to every handler on the root logger for belt-and-suspenders coverage
    for handler in root_logger.handlers:
        if not any(isinstance(f, CredentialMaskingFilter) for f in handler.filters):
            handler.addFilter(CredentialMaskingFilter())

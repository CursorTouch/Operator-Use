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
]


def mask_credentials(text: str) -> str:
    """Apply all credential masking patterns to a string."""
    for pattern, replacement in _MASK_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class CredentialMaskingFilter(logging.Filter):
    """Logging filter that redacts credential patterns from all log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_credentials(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: mask_credentials(str(v)) for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(mask_credentials(str(a)) for a in record.args)
        return True


def install_credential_masking() -> None:
    """Install credential masking on the root logger. Call once at startup."""
    root_logger = logging.getLogger()
    # Avoid double-installing
    if not any(isinstance(f, CredentialMaskingFilter) for f in root_logger.filters):
        root_logger.addFilter(CredentialMaskingFilter())

import re

from .base import ContentFilter


_CREDENTIAL_PATTERNS = [
    re.compile(r"(sk-[a-zA-Z0-9]{20,})"),
    re.compile(r"(gsk_[a-zA-Z0-9]{20,})"),
    re.compile(r"(AIza[a-zA-Z0-9_\-]{35})"),
    re.compile(r"(nvapi-[a-zA-Z0-9_\-]{20,})"),
    re.compile(r"(sk-or-v1-[a-zA-Z0-9]{20,})"),
    re.compile(r"(Bearer\s+[a-zA-Z0-9._\-]{20,})"),
]


class CredentialFilter(ContentFilter):
    """Masks API keys and tokens in log output and LLM context."""

    def filter(self, content: str) -> str:
        for pattern in _CREDENTIAL_PATTERNS:
            content = pattern.sub(lambda m: m.group()[:8] + "***REDACTED***", content)
        return content

    def is_safe(self, content: str) -> bool:
        return not any(p.search(content) for p in _CREDENTIAL_PATTERNS)

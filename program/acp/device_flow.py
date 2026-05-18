from __future__ import annotations

import json
import logging
import random
import secrets
import string
import time
from dataclasses import dataclass, field
from pathlib import Path

from program.acp.types import DeviceCodeResponse

logger = logging.getLogger(__name__)

_CODE_TTL = 600        # device code lives for 10 minutes
_USER_CODE_CHARS = string.ascii_uppercase + string.digits
_USER_CODE_LEN = 8     # e.g. "KQBG-MDJX" → displayed as 4-XXXX


@dataclass
class _PendingCode:
    device_code:      str
    user_code:        str
    verification_uri: str
    expires_at:       float
    approved:         bool = False
    token:            str | None = None


class DeviceFlowManager:
    """
    RFC 8628 Device Authorization Grant.

    Headless clients (CLI tools, remote agents) that cannot open a browser
    use the device flow:
        1. Client calls POST /auth/device → gets device_code + user_code
        2. Human opens verification_uri and enters user_code
        3. Client polls POST /auth/token with device_code
        4. Server returns access_token once approved

    Approved tokens are persisted to disk so they survive restarts.
    """

    def __init__(self, tokens_path: Path) -> None:
        self._tokens_path = tokens_path
        self._pending: dict[str, _PendingCode] = {}
        self._approved_tokens: set[str] = self._load_tokens()

    # ── Token persistence ─────────────────────────────────────────────────────

    def _load_tokens(self) -> set[str]:
        if not self._tokens_path.exists():
            return set()
        try:
            data = json.loads(self._tokens_path.read_text())
            return set(data.get('tokens', []))
        except Exception:
            return set()

    def _save_tokens(self) -> None:
        self._tokens_path.parent.mkdir(parents=True, exist_ok=True)
        self._tokens_path.write_text(
            json.dumps({'tokens': sorted(self._approved_tokens)}, indent=2)
        )
        self._tokens_path.chmod(0o600)

    # ── Code generation ───────────────────────────────────────────────────────

    @staticmethod
    def _make_user_code() -> str:
        """Generate a human-readable 8-char code like KQBG-MDJX."""
        raw = ''.join(random.choices(_USER_CODE_CHARS, k=_USER_CODE_LEN))
        return f'{raw[:4]}-{raw[4:]}'

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [k for k, v in self._pending.items() if v.expires_at < now]
        for k in expired:
            del self._pending[k]

    # ── Public API ────────────────────────────────────────────────────────────

    def create_code(self, verification_uri: str) -> DeviceCodeResponse:
        """Issue a new device code for a client that needs authorization."""
        self._purge_expired()
        device_code = secrets.token_hex(48)
        user_code   = self._make_user_code()
        pending = _PendingCode(
            device_code=device_code,
            user_code=user_code,
            verification_uri=verification_uri,
            expires_at=time.time() + _CODE_TTL,
        )
        self._pending[device_code] = pending
        logger.info('ACP device flow: issued code %s (user_code=%s)', device_code[:8], user_code)
        return DeviceCodeResponse(
            device_code=device_code,
            user_code=user_code,
            verification_uri=verification_uri,
            expires_in=_CODE_TTL,
            interval=5.0,
        )

    def approve(self, device_code: str) -> str | None:
        """Approve a pending device code and return the generated access token."""
        self._purge_expired()
        pending = self._pending.get(device_code)
        if not pending or pending.expires_at < time.time():
            return None
        token = secrets.token_urlsafe(48)
        pending.approved = True
        pending.token = token
        self._approved_tokens.add(token)
        self._save_tokens()
        logger.info('ACP device flow: approved code %s', device_code[:8])
        return token

    def poll(self, device_code: str) -> str | None:
        """Return the access token if the code has been approved, else None."""
        self._purge_expired()
        pending = self._pending.get(device_code)
        if pending and pending.approved and pending.token:
            return pending.token
        return None

    def validate_token(self, token: str) -> bool:
        """Return True if the token was issued by this device flow manager."""
        return token in self._approved_tokens

    def list_pending(self) -> list[_PendingCode]:
        """Return all unexpired, unapproved codes (for the approval UI)."""
        self._purge_expired()
        return [p for p in self._pending.values() if not p.approved]

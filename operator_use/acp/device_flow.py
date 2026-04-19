"""Device Authorization Grant manager (RFC 8628)."""

from __future__ import annotations

import json
import logging
import os
import random
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from operator_use.acp.models import DeviceCodeResponse

logger = logging.getLogger(__name__)

_CODE_CHARS = "BCDFGHJKLMNPQRSTVWXYZ23456789"  # excludes 0/O/I/1
_EXPIRES_IN = 600  # seconds


@dataclass
class _PendingCode:
    device_code: str
    user_code: str
    verification_uri: str
    expires_at: float          # time.monotonic() deadline
    access_token: str | None = None


class DeviceFlowManager:
    """Manages device codes, approvals, and token persistence."""

    def __init__(self, tokens_path: str) -> None:
        self._tokens_path = tokens_path
        self._pending: dict[str, _PendingCode] = {}   # device_code -> _PendingCode
        self._tokens: dict[str, str] = {}              # access_token -> approved_at ISO
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _purge_expired(self) -> None:
        now = time.monotonic()
        self._pending = {k: v for k, v in self._pending.items() if v.expires_at > now}

    def create_code(self, verification_uri: str) -> DeviceCodeResponse:
        self._purge_expired()
        device_code = secrets.token_hex(24)
        user_code = self._gen_user_code()
        entry = _PendingCode(
            device_code=device_code,
            user_code=user_code,
            verification_uri=verification_uri,
            expires_at=time.monotonic() + _EXPIRES_IN,
        )
        self._pending[device_code] = entry
        return DeviceCodeResponse(
            device_code=device_code,
            user_code=user_code,
            verification_uri=verification_uri,
            expires_in=_EXPIRES_IN,
            interval=5,
        )

    def approve(self, device_code: str) -> str | None:
        """Approve a pending code. Returns access_token or None if not found/expired."""
        entry = self._pending.get(device_code)
        if entry is None or time.monotonic() > entry.expires_at:
            return None
        if entry.access_token:
            return entry.access_token
        token = "op_" + secrets.token_hex(32)
        entry.access_token = token
        self._tokens[token] = datetime.now(timezone.utc).isoformat()
        self._save()
        return token

    def poll(self, device_code: str) -> str | None:
        """Return access_token if approved, None if still pending or unknown."""
        entry = self._pending.get(device_code)
        if entry is None or time.monotonic() > entry.expires_at:
            return None
        return entry.access_token

    def get_code_status(self, device_code: str) -> str:
        """Return 'approved', 'pending', or 'unknown_or_expired'."""
        entry = self._pending.get(device_code)
        if entry is None:
            return "unknown_or_expired"
        if time.monotonic() > entry.expires_at:
            return "unknown_or_expired"
        if entry.access_token:
            return "approved"
        return "pending"

    def validate_token(self, token: str) -> bool:
        return token in self._tokens

    def list_pending(self) -> list[_PendingCode]:
        now = time.monotonic()
        return [
            e for e in self._pending.values()
            if now <= e.expires_at and e.access_token is None
        ]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            with open(self._tokens_path) as f:
                self._tokens = json.load(f)
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning(f"Could not load ACP tokens from {self._tokens_path}: {exc}")

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._tokens_path) or ".", exist_ok=True)
            with open(self._tokens_path, "w") as f:
                json.dump(self._tokens, f, indent=2)
        except Exception as exc:
            logger.error(f"Could not save ACP tokens to {self._tokens_path}: {exc}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _gen_user_code() -> str:
        def part() -> str:
            return "".join(random.choices(_CODE_CHARS, k=4))
        return f"{part()}-{part()}"

from __future__ import annotations

import base64
import hashlib
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

TIMESTAMP_TOLERANCE = 30  # seconds — reject requests older/newer than this

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption,
        PrivateFormat, PublicFormat,
        load_pem_private_key,
    )
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False


def _require_crypto() -> None:
    if not _CRYPTO_AVAILABLE:
        raise ImportError(
            'cryptography>=42.0 is required for ACP provenance. '
            'Install it with: pip install "cryptography>=42.0"'
        )


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + '=' * (padding % 4))


def _canonical_payload(agent_id: str, timestamp: int, body: bytes) -> bytes:
    """Build the signed payload: agent_id + timestamp + SHA256(body)."""
    body_hash = hashlib.sha256(body).hexdigest()
    return f'{agent_id}\n{timestamp}\n{body_hash}'.encode()


class ACPProvenance:
    """
    Ed25519 signing and verification for ACP requests.

    Each Operator instance has a persistent keypair stored at
    ~/.program/acp_key.pem. The public key is served at
    GET /agents/{id}/pubkey so remote agents can verify signatures.

    Signing adds three headers to outgoing requests:
        X-ACP-Agent-ID   : <agent_id>
        X-ACP-Timestamp  : <unix_seconds_utc>
        X-ACP-Signature  : <base64url(ed25519_sign(payload))>
        X-ACP-Agent-URL  : <base_url>   (optional, for key discovery)

    Verification rejects:
        - Requests outside the 30-second timestamp window (replay attacks)
        - Requests with invalid or unrecognised signatures
    """

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        _require_crypto()
        self._private_key = private_key
        self._public_key: Ed25519PublicKey = private_key.public_key()

    # ── Construction ──────────────────────────────────────────────────────────

    @classmethod
    def generate(cls) -> ACPProvenance:
        """Generate a fresh Ed25519 keypair (not persisted)."""
        _require_crypto()
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def load_or_generate(cls, key_path: Path) -> ACPProvenance:
        """Load an existing PEM keypair or generate and persist a new one."""
        _require_crypto()
        if key_path.exists():
            pem = key_path.read_bytes()
            private_key = load_pem_private_key(pem, password=None)
            logger.debug('ACP: loaded keypair from %s', key_path)
        else:
            private_key = Ed25519PrivateKey.generate()
            pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_bytes(pem)
            key_path.chmod(0o600)
            logger.info('ACP: generated new keypair at %s', key_path)
        return cls(private_key)

    # ── Public key ────────────────────────────────────────────────────────────

    @property
    def public_key_b64(self) -> str:
        """Base64url-encoded raw 32-byte Ed25519 public key."""
        raw = self._public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        return _b64url_encode(raw)

    # ── Signing ───────────────────────────────────────────────────────────────

    def sign(self, agent_id: str, timestamp: int, body: bytes) -> str:
        """Return base64url-encoded Ed25519 signature over the canonical payload."""
        payload = _canonical_payload(agent_id, timestamp, body)
        sig = self._private_key.sign(payload)
        return _b64url_encode(sig)

    def auth_headers(
        self,
        agent_id: str,
        body: bytes,
        agent_url: str | None = None,
    ) -> dict[str, str]:
        """Build the X-ACP-* headers for an outgoing signed request."""
        timestamp = int(time.time())
        headers = {
            'X-ACP-Agent-ID':  agent_id,
            'X-ACP-Timestamp': str(timestamp),
            'X-ACP-Signature': self.sign(agent_id, timestamp, body),
        }
        if agent_url:
            headers['X-ACP-Agent-URL'] = agent_url
        return headers

    # ── Verification ──────────────────────────────────────────────────────────

    @staticmethod
    def verify(
        agent_id: str,
        timestamp: int,
        body: bytes,
        signature_b64: str,
        public_key_b64: str,
    ) -> bool:
        """
        Verify an incoming signed request.
        Returns True if the signature is valid and the timestamp is fresh.
        """
        _require_crypto()

        now = int(time.time())
        if abs(now - timestamp) > TIMESTAMP_TOLERANCE:
            logger.warning('ACP: rejecting request from %s — timestamp out of window', agent_id)
            return False

        try:
            pub_raw = _b64url_decode(public_key_b64)
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            pub_key = Ed25519PublicKey.from_public_bytes(pub_raw)
            sig = _b64url_decode(signature_b64)
            payload = _canonical_payload(agent_id, timestamp, body)
            pub_key.verify(sig, payload)
            return True
        except Exception:
            logger.warning('ACP: signature verification failed for agent %s', agent_id)
            return False

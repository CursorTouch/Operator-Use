"""ACP Provenance — Ed25519 key management and request signing/verification.

Each Operator instance generates (or loads) a persistent Ed25519 keypair.
Every outgoing HTTP ACP request is signed; every incoming request can be
verified against the sender's published public key.

Signing scheme
--------------
The signed payload is a canonical string:

    {agent_id}\\n{timestamp}\\n{sha256_hex(body)}

Headers added to every signed request:

    X-ACP-Agent-ID   : <agent_id>
    X-ACP-Timestamp  : <unix_seconds_utc>
    X-ACP-Signature  : <base64url(ed25519_sign(private_key, payload))>

Optional auto-discovery header (lets the receiver fetch the sender's pubkey):

    X-ACP-Agent-URL  : <base_url_of_sender>

Verification steps
------------------
1. Read X-ACP-Agent-ID, X-ACP-Timestamp, X-ACP-Signature.
2. Reject if |now - timestamp| > TIMESTAMP_TOLERANCE_SECS (replay protection).
3. Look up the sender's public key (from trusted_agents config or by fetching
   {X-ACP-Agent-URL}/agents/{agent_id}/pubkey).
4. Verify the Ed25519 signature against the canonical payload.

Dependencies
------------
    pip install cryptography
"""

from __future__ import annotations

import base64
import hashlib
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Reject requests whose timestamp differs from now by more than this many seconds
TIMESTAMP_TOLERANCE_SECS = 30


# ---------------------------------------------------------------------------
# Internal: lazy import of cryptography (optional dep)
# ---------------------------------------------------------------------------


def _ed25519():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )

        return Ed25519PrivateKey, Ed25519PublicKey
    except ImportError:
        raise ImportError(
            "The 'cryptography' package is required for ACP provenance signatures. "
            "Install it with: pip install cryptography"
        )


def _serialization():
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
        load_pem_private_key,
    )

    return Encoding, NoEncryption, PrivateFormat, PublicFormat, load_pem_private_key


# ---------------------------------------------------------------------------
# ACPProvenance
# ---------------------------------------------------------------------------


class ACPProvenance:
    """Holds an Ed25519 keypair and provides sign/verify helpers."""

    def __init__(self, private_key) -> None:
        self._private_key = private_key
        self._public_key = private_key.public_key()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def generate(cls) -> "ACPProvenance":
        """Generate a fresh Ed25519 keypair (ephemeral, not persisted)."""
        Ed25519PrivateKey, _ = _ed25519()
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def load_or_generate(cls, key_path: str | Path) -> "ACPProvenance":
        """Load keypair from PEM file, creating it if it doesn't exist."""
        path = Path(key_path)
        Encoding, NoEncryption, PrivateFormat, _, load_pem_private_key = _serialization()
        Ed25519PrivateKey, _ = _ed25519()

        if path.exists():
            pem = path.read_bytes()
            private_key = load_pem_private_key(pem, password=None)
            logger.debug(f"ACP provenance: loaded keypair from {path}")
        else:
            private_key = Ed25519PrivateKey.generate()
            pem = private_key.private_bytes(
                encoding=Encoding.PEM,
                format=PrivateFormat.PKCS8,
                encryption_algorithm=NoEncryption(),
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(pem)
            logger.info(f"ACP provenance: generated new keypair at {path}")

        return cls(private_key)

    # ------------------------------------------------------------------
    # Public key serialisation
    # ------------------------------------------------------------------

    @property
    def public_key_b64(self) -> str:
        """Base64url-encoded raw public key (32 bytes)."""
        _, _, _, PublicFormat, _ = _serialization()
        Encoding, *_ = _serialization()
        raw = self._public_key.public_bytes(
            encoding=Encoding.Raw,
            format=PublicFormat.Raw,
        )
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    # ------------------------------------------------------------------
    # Sign
    # ------------------------------------------------------------------

    def sign(self, agent_id: str, timestamp: int, body: bytes) -> str:
        """Return a base64url signature for the canonical payload."""
        payload = _canonical_payload(agent_id, timestamp, body)
        sig = self._private_key.sign(payload)
        return base64.urlsafe_b64encode(sig).rstrip(b"=").decode()

    def auth_headers(
        self,
        agent_id: str,
        body: bytes,
        agent_url: str | None = None,
    ) -> dict[str, str]:
        """Build X-ACP-* headers for an outgoing request."""
        ts = int(time.time())
        headers = {
            "X-ACP-Agent-ID": agent_id,
            "X-ACP-Timestamp": str(ts),
            "X-ACP-Signature": self.sign(agent_id, ts, body),
        }
        if agent_url:
            headers["X-ACP-Agent-URL"] = agent_url
        return headers

    # ------------------------------------------------------------------
    # Verify
    # ------------------------------------------------------------------

    @staticmethod
    def verify(
        agent_id: str,
        timestamp: int,
        body: bytes,
        signature_b64: str,
        public_key_b64: str,
    ) -> bool:
        """Verify an Ed25519 signature. Returns True if valid."""
        _, Ed25519PublicKey = _ed25519()
        _, _, _, PublicFormat, _ = _serialization()
        Encoding, *_ = _serialization()

        # Replay protection
        age = abs(time.time() - timestamp)
        if age > TIMESTAMP_TOLERANCE_SECS:
            logger.warning(f"ACP signature rejected: timestamp too old/future ({age:.0f}s)")
            return False

        try:
            raw_pub = base64.urlsafe_b64decode(_pad_b64(public_key_b64))
            pub_key = Ed25519PublicKey.from_public_bytes(raw_pub)
            sig = base64.urlsafe_b64decode(_pad_b64(signature_b64))
            payload = _canonical_payload(agent_id, timestamp, body)
            pub_key.verify(sig, payload)
            return True
        except Exception as exc:
            logger.warning(f"ACP signature verification failed: {exc}")
            return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _canonical_payload(agent_id: str, timestamp: int, body: bytes) -> bytes:
    """Canonical signed payload: agent_id\\ntimestamp\\nsha256_hex(body)"""
    body_hash = hashlib.sha256(body).hexdigest()
    return f"{agent_id}\n{timestamp}\n{body_hash}".encode()


def _pad_b64(s: str) -> str:
    """Re-add base64url padding stripped by rstrip('=')."""
    return s + "=" * (-len(s) % 4)


# ---------------------------------------------------------------------------
# Public key fetch helper (for verifying remote agents)
# ---------------------------------------------------------------------------


async def fetch_public_key(agent_url: str, agent_id: str) -> str | None:
    """Fetch a remote agent's Ed25519 public key.

    Calls GET {agent_url}/agents/{agent_id}/pubkey and returns the
    base64url-encoded public key string, or None on failure.
    """
    try:
        import aiohttp

        url = f"{agent_url.rstrip('/')}/agents/{agent_id}/pubkey"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("public_key")
    except Exception as exc:
        logger.debug(f"Failed to fetch pubkey for {agent_id} from {agent_url}: {exc}")
    return None

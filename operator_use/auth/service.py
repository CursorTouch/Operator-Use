"""AuthStore: reads and writes ~/.operator-use/auth.json.

Schema
------
{
  "<provider>": {
    "type": "key",
    "key": "<api-key>"
  },
  "<provider>": {
    "type": "oauth",
    "access_token": "...",
    "refresh_token": "...",   # optional
    "expires_at": "..."       # ISO-8601, optional
  }
}
"""

import json
import os
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

# Maps provider name → the env var providers read from os.environ
PROVIDER_ENV_MAP: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "xai": "XAI_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "open_router": "OPENROUTER_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "deepgram": "DEEPGRAM_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
    "sarvam": "SARVAM_API_KEY",
}


class KeyCredential(BaseModel):
    type: Literal["key"]
    key: str


class OAuthCredential(BaseModel):
    type: Literal["oauth"]
    access_token: str
    refresh_token: str | None = None
    expires_at: str | None = None


Credential = Annotated[
    Union[KeyCredential, OAuthCredential],
    Field(discriminator="type"),
]


class AuthStore:
    """Persistent credential store backed by auth.json."""

    def __init__(self, auth_file: Path) -> None:
        self.auth_file = auth_file

    def _read_raw(self) -> dict:
        if not self.auth_file.exists():
            return {}
        try:
            return json.loads(self.auth_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_raw(self, data: dict) -> None:
        self.auth_file.parent.mkdir(parents=True, exist_ok=True)
        self.auth_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get(self, provider: str) -> Credential | None:
        raw = self._read_raw().get(provider)
        if raw is None:
            return None
        t = raw.get("type")
        if t == "key":
            return KeyCredential(**raw)
        if t == "oauth":
            return OAuthCredential(**raw)
        return None

    def set_key(self, provider: str, key: str) -> None:
        data = self._read_raw()
        data[provider] = {"type": "key", "key": key}
        self._write_raw(data)

    def set_oauth(
        self,
        provider: str,
        access_token: str,
        refresh_token: str | None = None,
        expires_at: str | None = None,
    ) -> None:
        data = self._read_raw()
        entry: dict = {"type": "oauth", "access_token": access_token}
        if refresh_token is not None:
            entry["refresh_token"] = refresh_token
        if expires_at is not None:
            entry["expires_at"] = expires_at
        data[provider] = entry
        self._write_raw(data)

    def remove(self, provider: str) -> None:
        data = self._read_raw()
        data.pop(provider, None)
        self._write_raw(data)

    def all(self) -> dict[str, Credential]:
        out: dict[str, Credential] = {}
        for provider, raw in self._read_raw().items():
            t = raw.get("type")
            if t == "key":
                out[provider] = KeyCredential(**raw)
            elif t == "oauth":
                out[provider] = OAuthCredential(**raw)
        return out

    def inject_env(self) -> None:
        """Set os.environ from stored key credentials (only if not already set)."""
        for provider, cred in self.all().items():
            if isinstance(cred, KeyCredential):
                env_var = PROVIDER_ENV_MAP.get(provider)
                if env_var and not os.environ.get(env_var):
                    os.environ[env_var] = cred.key

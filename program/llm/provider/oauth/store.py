from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from program.llm.provider.oauth.types import OAuthCredentials

_STORE_PATH = Path.home() / ".program" / "auth.json"


def _load_store() -> dict:
    if not _STORE_PATH.exists():
        return {}
    try:
        return json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_store(store: dict) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STORE_PATH.write_text(json.dumps(store, indent=2), encoding="utf-8")


def load_credentials(provider_id: str) -> Optional[OAuthCredentials]:
    data = _load_store().get(provider_id)
    if not data:
        return None
    try:
        return OAuthCredentials(
            access=data["access"],
            refresh=data["refresh"],
            expires=data["expires"],
            account_id=data.get("account_id", ""),
        )
    except Exception:
        return None


def save_credentials(provider_id: str, credentials: OAuthCredentials) -> None:
    store = _load_store()
    store[provider_id] = {
        "access": credentials.access,
        "refresh": credentials.refresh,
        "expires": credentials.expires,
        "account_id": credentials.account_id,
    }
    _save_store(store)


def delete_credentials(provider_id: str) -> None:
    store = _load_store()
    if provider_id in store:
        del store[provider_id]
        _save_store(store)

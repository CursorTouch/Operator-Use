"""Registry of OAuth provider configurations."""

import os
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class OAuthProviderConfig:
    name: str
    flow: Literal["pkce", "device"]
    token_url: str
    scopes: list[str] = field(default_factory=list)
    # PKCE-specific
    auth_url: str = ""
    redirect_port: int = 9743
    extra_auth_params: dict = field(default_factory=dict)
    # Device-flow-specific
    device_code_url: str = ""
    # Credentials (may be overridden by env vars)
    client_id: str = ""
    client_secret: str = ""  # empty for pure public PKCE clients


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def get_provider(name: str) -> OAuthProviderConfig | None:
    """Return a fully resolved provider config (env vars applied) or None."""
    cfg = _REGISTRY.get(name)
    if cfg is None:
        return None
    # Apply env-var overrides for client credentials
    _apply_env_overrides(name, cfg)
    return cfg


def _apply_env_overrides(name: str, cfg: OAuthProviderConfig) -> None:
    prefix = name.upper().replace("-", "_")
    if not cfg.client_id:
        cfg.client_id = _env(f"{prefix}_CLIENT_ID")
    if not cfg.client_secret:
        cfg.client_secret = _env(f"{prefix}_CLIENT_SECRET")


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, OAuthProviderConfig] = {
    "antigravity": OAuthProviderConfig(
        name="Google (Cloud Code Assist / Antigravity)",
        flow="pkce",
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=[
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/cclog",
            "https://www.googleapis.com/auth/experimentsandconfigs",
        ],
        redirect_port=36742,
        extra_auth_params={"access_type": "offline", "prompt": "consent"},
    ),
    "github_copilot": OAuthProviderConfig(
        name="GitHub Copilot",
        flow="device",
        device_code_url="https://github.com/login/device/code",
        token_url="https://github.com/login/oauth/access_token",
        scopes=["copilot"],
        client_id="Iv1.b507a08c87ecfe98",
    ),
}

OAUTH_PROVIDER_NAMES: list[str] = list(_REGISTRY.keys())

"""OAuthManager: login / logout / refresh with automatic PKCE re-login on expiry."""

import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from operator_use.auth.pkce import generate_pkce_pair, open_browser, wait_for_callback
from operator_use.auth.providers import OAuthProviderConfig, get_provider
from operator_use.auth.service import AuthStore, OAuthCredential

logger = logging.getLogger(__name__)

# Refresh this many seconds before the token actually expires
_REFRESH_BUFFER = 300


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expires_at_iso(expires_in: int) -> str:
    ts = time.time() + expires_in - _REFRESH_BUFFER
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _is_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        dt = datetime.fromisoformat(expires_at)
        return dt.timestamp() <= time.time()
    except Exception:
        return False


class OAuthManager:
    """Handles login / logout / refresh for OAuth providers.

    All tokens are persisted in AuthStore (auth.json).
    When an access_token is expired:
      1. Auto-refresh using the refresh_token (if present).
      2. If refresh fails or no refresh_token, automatically re-run PKCE login
         so the user never has to intervene manually.
    """

    def __init__(self, store: AuthStore) -> None:
        self.store = store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def login(self, provider_name: str) -> OAuthCredential:
        """Run the full login flow for the given provider and persist the result."""
        cfg = self._require_provider(provider_name)
        if cfg.flow == "pkce":
            cred = self._pkce_login(provider_name, cfg)
        elif cfg.flow == "device":
            cred = self._device_login(provider_name, cfg)
        else:
            raise ValueError(f"Unknown flow '{cfg.flow}' for provider '{provider_name}'.")
        self._save(provider_name, cred)
        return cred

    def logout(self, provider_name: str) -> None:
        """Remove stored credentials for the given provider."""
        self.store.remove(provider_name)
        logger.info("Logged out from '%s'.", provider_name)

    def refresh(self, provider_name: str) -> OAuthCredential:
        """Force-refresh the access token using the stored refresh_token.

        If no refresh_token exists or the refresh request fails, falls back to
        a full PKCE login so the user is never permanently stuck.
        """
        cred = self._load_oauth(provider_name)
        if cred and cred.refresh_token:
            refreshed = self._do_refresh(provider_name, cred.refresh_token)
            if refreshed:
                self._save(provider_name, refreshed)
                return refreshed
        logger.warning("Refresh failed for '%s', re-running login.", provider_name)
        return self.login(provider_name)

    def get_token(self, provider_name: str) -> str:
        """Return a valid access_token, refreshing or re-logging in automatically."""
        cred = self._load_oauth(provider_name)

        if cred is None:
            logger.info("No credentials for '%s', starting login.", provider_name)
            cred = self.login(provider_name)

        if _is_expired(cred.expires_at):
            logger.info("Token expired for '%s', refreshing.", provider_name)
            cred = self.refresh(provider_name)

        return cred.access_token

    # ------------------------------------------------------------------
    # PKCE flow
    # ------------------------------------------------------------------

    def _pkce_login(self, provider_name: str, cfg: OAuthProviderConfig) -> OAuthCredential:
        if not cfg.client_id:
            raise RuntimeError(
                f"No client_id configured for '{provider_name}'. "
                f"Set {provider_name.upper()}_CLIENT_ID in your environment."
            )

        verifier, challenge = generate_pkce_pair()
        redirect_uri = f"http://localhost:{cfg.redirect_port}/oauth-callback"

        params = {
            "client_id": cfg.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(cfg.scopes),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            **cfg.extra_auth_params,
        }
        auth_url = cfg.auth_url + "?" + urlencode(params)
        open_browser(auth_url)

        callback = wait_for_callback(cfg.redirect_port)
        code = callback.get("code")
        if not code:
            raise RuntimeError(f"No authorization code received for '{provider_name}'.")

        return self._exchange_pkce_code(cfg, code, verifier, redirect_uri)

    def _exchange_pkce_code(
        self,
        cfg: OAuthProviderConfig,
        code: str,
        verifier: str,
        redirect_uri: str,
    ) -> OAuthCredential:
        data: dict = {
            "client_id": cfg.client_id,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }
        if cfg.client_secret:
            data["client_secret"] = cfg.client_secret

        r = httpx.post(
            cfg.token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )
        r.raise_for_status()
        tokens = r.json()
        return OAuthCredential(
            type="oauth",
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            expires_at=_expires_at_iso(tokens.get("expires_in", 3600)),
        )

    # ------------------------------------------------------------------
    # Device flow
    # ------------------------------------------------------------------

    def _device_login(self, provider_name: str, cfg: OAuthProviderConfig) -> OAuthCredential:
        import webbrowser

        r = httpx.post(
            cfg.device_code_url,
            data={"client_id": cfg.client_id, "scope": " ".join(cfg.scopes)},
            headers={"Accept": "application/json"},
            timeout=15.0,
        )
        r.raise_for_status()
        device = r.json()

        user_code = device["user_code"]
        device_code = device["device_code"]
        verification_uri = device.get("verification_uri", "https://github.com/login/device")
        interval = device.get("interval", 5)
        expires_in = device.get("expires_in", 900)

        print(f"\n  Visit: {verification_uri}")
        print(f"  Enter code: {user_code}\n")
        try:
            webbrowser.open(verification_uri)
        except Exception:
            pass

        deadline = time.time() + expires_in
        while time.time() < deadline:
            time.sleep(interval)
            resp = httpx.post(
                cfg.token_url,
                data={
                    "client_id": cfg.client_id,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                headers={"Accept": "application/json"},
                timeout=15.0,
            )
            payload = resp.json()
            if payload.get("access_token"):
                return OAuthCredential(
                    type="oauth",
                    access_token=payload["access_token"],
                    refresh_token=payload.get("refresh_token"),
                    expires_at=_expires_at_iso(payload.get("expires_in", 28800)),
                )
            error = payload.get("error", "")
            if error == "authorization_pending":
                continue
            elif error == "slow_down":
                interval += 5
            elif error in ("expired_token", "access_denied"):
                raise RuntimeError(f"Device auth failed: {error}")

        raise RuntimeError(f"Device auth timed out for '{provider_name}'.")

    # ------------------------------------------------------------------
    # Token refresh
    # ------------------------------------------------------------------

    def _do_refresh(
        self, provider_name: str, refresh_token: str
    ) -> OAuthCredential | None:
        cfg = self._require_provider(provider_name)
        try:
            data: dict = {
                "client_id": cfg.client_id,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
            if cfg.client_secret:
                data["client_secret"] = cfg.client_secret

            r = httpx.post(
                cfg.token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30.0,
            )
            if r.status_code != 200:
                logger.warning("Refresh request failed: %s %s", r.status_code, r.text[:200])
                return None
            tokens = r.json()
            return OAuthCredential(
                type="oauth",
                access_token=tokens["access_token"],
                refresh_token=tokens.get("refresh_token") or refresh_token,
                expires_at=_expires_at_iso(tokens.get("expires_in", 3600)),
            )
        except Exception as e:
            logger.warning("Refresh exception for '%s': %s", provider_name, e)
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_oauth(self, provider_name: str) -> OAuthCredential | None:
        cred = self.store.get(provider_name)
        if isinstance(cred, OAuthCredential):
            return cred
        return None

    def _save(self, provider_name: str, cred: OAuthCredential) -> None:
        self.store.set_oauth(
            provider_name,
            access_token=cred.access_token,
            refresh_token=cred.refresh_token,
            expires_at=cred.expires_at,
        )

    def _require_provider(self, name: str) -> OAuthProviderConfig:
        cfg = get_provider(name)
        if cfg is None:
            raise ValueError(
                f"Unknown OAuth provider '{name}'. "
                f"Add it to operator_use/auth/providers.py first."
            )
        return cfg

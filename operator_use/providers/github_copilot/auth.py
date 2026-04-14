"""
GitHub Copilot OAuth authentication.

Uses GitHub's Device OAuth flow to obtain a GitHub OAuth token,
then exchanges it for a short-lived Copilot API token (expires every 25 min).

Token storage: ~/.config/operator/github_copilot_auth.json
"""

import json
import logging
import time
import webbrowser
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

CLIENT_ID = "Iv1.b507a08c87ecfe98"
DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"

_AUTH_PATH = Path.home() / ".config" / "operator" / "github_copilot_auth.json"


def load_auth() -> Optional[dict]:
    if not _AUTH_PATH.exists():
        return None
    try:
        return json.loads(_AUTH_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Cannot read GitHub Copilot auth: {e}")
        return None


def save_auth(data: dict) -> None:
    _AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    _AUTH_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _exchange_copilot_token(github_token: str) -> dict:
    """Exchange a GitHub OAuth token for a short-lived Copilot API token."""
    r = httpx.get(
        COPILOT_TOKEN_URL,
        headers={
            "Authorization": f"token {github_token}",
            "Accept": "application/json",
        },
        timeout=30.0,
    )
    r.raise_for_status()
    data = r.json()
    # Response: {"token": "...", "expires_at": "2024-..."}
    token = data.get("token", "")
    expires_at_str = data.get("expires_at", "")
    # Parse ISO timestamp to epoch seconds
    expires_at = 0.0
    if expires_at_str:
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            expires_at = dt.timestamp()
        except Exception:
            expires_at = time.time() + 1500  # fallback: 25 minutes
    return {"token": token, "expires_at": expires_at}


async def _async_exchange_copilot_token(github_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            COPILOT_TOKEN_URL,
            headers={
                "Authorization": f"token {github_token}",
                "Accept": "application/json",
            },
            timeout=30.0,
        )
        r.raise_for_status()
        data = r.json()
        token = data.get("token", "")
        expires_at_str = data.get("expires_at", "")
        expires_at = 0.0
        if expires_at_str:
            try:
                from datetime import datetime

                dt = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                expires_at = dt.timestamp()
            except Exception:
                expires_at = time.time() + 1500
        return {"token": token, "expires_at": expires_at}


def get_copilot_token(auth: dict) -> str:
    """Return a valid Copilot API token, refreshing if expiring within 60s."""
    expires_at = auth.get("copilot_expires_at", 0)
    if expires_at > time.time() + 60:
        return auth["copilot_token"]

    logger.debug("Copilot token expired/missing, refreshing...")
    result = _exchange_copilot_token(auth["github_token"])
    auth["copilot_token"] = result["token"]
    auth["copilot_expires_at"] = result["expires_at"]
    save_auth(auth)
    return auth["copilot_token"]


async def async_get_copilot_token(auth: dict) -> str:
    """Async version of get_copilot_token."""
    expires_at = auth.get("copilot_expires_at", 0)
    if expires_at > time.time() + 60:
        return auth["copilot_token"]

    logger.debug("Copilot token expired/missing, refreshing async...")
    result = await _async_exchange_copilot_token(auth["github_token"])
    auth["copilot_token"] = result["token"]
    auth["copilot_expires_at"] = result["expires_at"]
    save_auth(auth)
    return auth["copilot_token"]


def login() -> dict:
    """
    Run the GitHub Device OAuth flow and obtain Copilot credentials.

    Opens the browser for the user to authorize, polls for the token,
    then exchanges it for a Copilot API token. Saves to auth file.
    """
    # Step 1: Request device code
    r = httpx.post(
        DEVICE_CODE_URL,
        data={"client_id": CLIENT_ID, "scope": "copilot"},
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

    print(f"\n  Go to: {verification_uri}")
    print(f"  Enter code: {user_code}\n")
    try:
        webbrowser.open(verification_uri)
    except Exception:
        pass

    # Step 2: Poll for access token
    deadline = time.time() + expires_in
    github_token = None
    while time.time() < deadline:
        time.sleep(interval)
        resp = httpx.post(
            GITHUB_TOKEN_URL,
            data={
                "client_id": CLIENT_ID,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
            timeout=15.0,
        )
        data = resp.json()
        if data.get("access_token"):
            github_token = data["access_token"]
            break
        error = data.get("error", "")
        if error == "authorization_pending":
            continue
        elif error == "slow_down":
            interval += 5
        elif error in ("expired_token", "access_denied"):
            raise RuntimeError(f"GitHub auth failed: {error}")

    if not github_token:
        raise RuntimeError("GitHub device auth timed out.")

    # Step 3: Exchange for Copilot token
    copilot = _exchange_copilot_token(github_token)

    auth = {
        "github_token": github_token,
        "copilot_token": copilot["token"],
        "copilot_expires_at": copilot["expires_at"],
    }
    save_auth(auth)
    return auth

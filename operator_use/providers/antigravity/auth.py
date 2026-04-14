"""
Antigravity (Google Cloud Code Assist) OAuth credential management.

Uses Antigravity-specific OAuth credentials (from @mariozechner/pi-ai package)
to access Google's Cloud Code Assist API, which provides Gemini and Claude models.

Credentials are stored at ~/.config/operator/antigravity_auth.json.
Run: operator auth antigravity
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse
from dotenv import load_dotenv

import httpx

load_dotenv()

logger = logging.getLogger(__name__)

# OAuth endpoints
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo?alt=json"

# Antigravity-specific OAuth credentials (from @mariozechner/pi-ai google-antigravity.js)
# Set ANTIGRAVITY_CLIENT_ID and ANTIGRAVITY_CLIENT_SECRET in your environment or .env file.
_CLIENT_ID = os.environ.get("ANTIGRAVITY_CLIENT_ID", "")
_CLIENT_SECRET = os.environ.get("ANTIGRAVITY_CLIENT_SECRET", "")

# Cloud Code Assist endpoints (prod first, then sandbox fallback)
LOAD_CODE_ASSIST_ENDPOINTS = [
    "https://cloudcode-pa.googleapis.com",
    "https://daily-cloudcode-pa.sandbox.googleapis.com",
]

# Fallback project ID when discovery fails
DEFAULT_PROJECT_ID = "rising-fact-p41fc"

# OAuth scopes (from @mariozechner/pi-ai google-antigravity.js)
SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]

REDIRECT_URI = "http://localhost:36742/oauth-callback"

_AUTH_PATH = Path.home() / ".config" / "operator" / "antigravity_auth.json"


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ---------------------------------------------------------------------------
# Credential file I/O
# ---------------------------------------------------------------------------


def load_auth() -> Optional[dict]:
    if _AUTH_PATH.exists():
        try:
            return json.loads(_AUTH_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Cannot read Antigravity auth: {e}")
    return None


def save_auth(data: dict) -> None:
    _AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    _AUTH_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------


def refresh_token(refresh_tok: str) -> Optional[dict]:
    try:
        r = httpx.post(
            TOKEN_URL,
            data={
                "client_id": _CLIENT_ID,
                "client_secret": _CLIENT_SECRET,
                "refresh_token": refresh_tok,
                "grant_type": "refresh_token",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.error(f"Antigravity token refresh failed: {e}")
    return None


async def async_refresh_token(refresh_tok: str) -> Optional[dict]:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                TOKEN_URL,
                data={
                    "client_id": _CLIENT_ID,
                    "client_secret": _CLIENT_SECRET,
                    "refresh_token": refresh_tok,
                    "grant_type": "refresh_token",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30.0,
            )
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.error(f"Antigravity async token refresh failed: {e}")
    return None


def fetch_project_id(access_token: str) -> str:
    """Discover Cloud Code Assist project ID via loadCodeAssist."""
    ua_platform = "windows/amd64" if os.name == "nt" else "darwin/arm64"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": f"antigravity/1.26.0 {ua_platform}",
        "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
        "Client-Metadata": json.dumps(
            {
                "ideType": "ANTIGRAVITY",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI",
            }
        ),
    }
    body = {
        "metadata": {
            "ideType": "ANTIGRAVITY",
            "platform": "PLATFORM_UNSPECIFIED",
            "pluginType": "GEMINI",
        }
    }

    for endpoint in LOAD_CODE_ASSIST_ENDPOINTS:
        try:
            r = httpx.post(
                f"{endpoint}/v1internal:loadCodeAssist", json=body, headers=headers, timeout=15.0
            )
            logger.debug("loadCodeAssist %s -> %s", endpoint, r.status_code)
            if r.status_code == 200:
                data = r.json()
                project = data.get("cloudaicompanionProject", "")
                if isinstance(project, str) and project:
                    return project
                if isinstance(project, dict) and project.get("id"):
                    return project["id"]
        except Exception:
            continue

    env_project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get(
        "GOOGLE_CLOUD_PROJECT_ID", ""
    )
    return env_project or DEFAULT_PROJECT_ID


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------


def _onboard_user(access_token: str, project_id: str) -> None:
    """Call onboardUser to activate the account for API access (safe to call repeatedly)."""
    ua_platform = "windows/amd64" if os.name == "nt" else "darwin/arm64"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": f"antigravity/1.26.0 {ua_platform}",
        "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
        "Client-Metadata": json.dumps(
            {
                "ideType": "ANTIGRAVITY",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI",
            }
        ),
    }
    body = {
        "cloudaicompanionProject": project_id,
        "metadata": {
            "ideType": "ANTIGRAVITY",
            "platform": "PLATFORM_UNSPECIFIED",
            "pluginType": "GEMINI",
        },
    }
    for endpoint in LOAD_CODE_ASSIST_ENDPOINTS:
        try:
            r = httpx.post(
                f"{endpoint}/v1internal:onboardUser", json=body, headers=headers, timeout=15.0
            )
            if r.status_code == 200:
                logger.debug("Antigravity onboardUser succeeded via %s", endpoint)
                return
        except Exception:
            continue
    logger.warning("Antigravity onboardUser failed (non-fatal, may still work)")


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def login() -> dict:
    """
    Run the Antigravity OAuth PKCE flow interactively.
    Uses Antigravity-specific credentials (from @mariozechner/pi-ai).
    """
    verifier, challenge = _pkce_pair()

    params = {
        "client_id": _CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier,
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = AUTH_URL + "?" + urlencode(params)

    captured: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = parse_qs(urlparse(self.path).query)
            captured["code"] = qs.get("code", [None])[0]
            captured["state"] = qs.get("state", [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>Antigravity login complete. You may close this tab.</h2>")

        def log_message(self, *args):
            pass

    server = HTTPServer(("localhost", 36742), Handler)
    thread = Thread(target=server.handle_request)
    thread.daemon = True
    thread.start()

    print("\nOpening browser for Antigravity login...")
    print(f"If it doesn't open automatically, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)
    thread.join(timeout=120)

    code = captured.get("code")
    if not code:
        raise RuntimeError("No authorization code received. Login timed out.")

    r = httpx.post(
        TOKEN_URL,
        data={
            "client_id": _CLIENT_ID,
            "client_secret": _CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30.0,
    )
    r.raise_for_status()
    tokens = r.json()

    access_token = tokens["access_token"]
    project_id = fetch_project_id(access_token)
    _onboard_user(access_token, project_id)

    auth = {
        "access_token": access_token,
        "refresh_token": tokens["refresh_token"],
        "expires_at": time.time() + tokens.get("expires_in", 3600) - 300,
        "project_id": project_id,
    }
    save_auth(auth)
    print(f"Antigravity login successful. Project: {project_id}")
    print(f"Credentials saved to {_AUTH_PATH}")
    return auth


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "login":
        login()
    else:
        print("Usage: python -m operator_use.providers.antigravity.auth login")

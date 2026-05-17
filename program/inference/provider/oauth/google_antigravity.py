"""
Google Antigravity OAuth flow — standard authorization code + local callback server.

The access token is used as a Bearer token for calls to cloudcode-pa.googleapis.com,
giving access to Claude and Gemini models via Google's Antigravity IDE quota.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

import certifi

from dataclasses import dataclass
from program.inference.provider.types import OAuthProvider
from program.inference.provider.oauth.types import OAuthAuthInfo, OAuthCredential, OAuthLoginCallbacks, OAuthPrompt, AbortSignal

__all__ = ["GoogleAntigravityOAuthProvider"]

_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
CLIENT_SECRET = "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"
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo"
CALLBACK_HOST = None  # binds to all interfaces (IPv4 + IPv6)
CALLBACK_PORT = 51121
CALLBACK_PATH = "/oauth-callback"
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"
SCOPES = " ".join([
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
])

_SUCCESS_HTML = b"""<!DOCTYPE html><html><head><title>Auth complete</title></head><body>
<h2>Authentication successful!</h2>
<p>You can close this window and return to the application.</p>
</body></html>"""

_ERROR_HTML = b"""<!DOCTYPE html><html><head><title>Auth failed</title></head><body>
<h2>Authentication failed</h2>
<p>An error occurred. Please try again.</p>
</body></html>"""



def _parse_authorization_input(value: str) -> tuple[Optional[str], Optional[str]]:
    value = value.strip()
    if not value:
        return None, None
    try:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme in ("http", "https"):
            params = urllib.parse.parse_qs(parsed.query)
            return params.get("code", [None])[0], params.get("state", [None])[0]
    except Exception:
        pass
    if "code=" in value:
        params = urllib.parse.parse_qs(value)
        return params.get("code", [None])[0], params.get("state", [None])[0]
    return value, None


def _build_authorization_url(state: str) -> str:
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _post_form(url: str, body: dict) -> dict:
    data = urllib.parse.urlencode(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CONTEXT, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        raise RuntimeError(f"Request failed ({e.code}): {body_text}") from e


def _exchange_code(code: str, state: str) -> dict:
    return _post_form(TOKEN_URL, {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI,
    })


def _refresh_token_sync(refresh_token: str) -> dict:
    return _post_form(TOKEN_URL, {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
    })


def _validate_token_sync(access_token: str) -> bool:
    req = urllib.request.Request(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CONTEXT, timeout=10) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        return e.code not in (401, 403)
    except Exception:
        return False


def _parse_token_response(data: dict) -> tuple[str, str, int]:
    """Returns (access_token, refresh_token, expires_ms)."""
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    expires_in = data.get("expires_in")
    if not access or not isinstance(expires_in, (int, float)):
        raise ValueError(f"Token response missing fields: {data}")
    if not refresh:
        # Google doesn't always return a new refresh_token on refresh
        refresh = ""
    expires_ms = int(time.time() * 1000) + int(expires_in) * 1000 - 5 * 60 * 1000
    return access, refresh, expires_ms


async def _start_local_server(expected_state: str) -> tuple[asyncio.Server, asyncio.Future[str]]:
    loop = asyncio.get_running_loop()
    code_future: asyncio.Future[str] = loop.create_future()

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await reader.read(4096)
            line = raw.decode(errors="replace").split("\r\n")[0]
            parts = line.split(" ")
            if len(parts) < 2:
                writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                await writer.drain()
                return

            parsed = urllib.parse.urlparse(parts[1])
            params = urllib.parse.parse_qs(parsed.query)

            if parsed.path == CALLBACK_PATH:
                recv_state = params.get("state", [None])[0]
                code = params.get("code", [None])[0]
                error = params.get("error", [None])[0]

                if error:
                    writer.write(
                        b"HTTP/1.1 400 Bad Request\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
                        + _ERROR_HTML
                    )
                elif recv_state == expected_state and code:
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
                        + _SUCCESS_HTML
                    )
                    if not code_future.done():
                        code_future.set_result(code)
                else:
                    writer.write(
                        b"HTTP/1.1 400 Bad Request\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
                        + _ERROR_HTML
                    )
            else:
                writer.write(b"HTTP/1.1 404 Not Found\r\n\r\n")

            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(_handle, CALLBACK_HOST, CALLBACK_PORT)
    return server, code_future


async def login_antigravity(callbacks: OAuthLoginCallbacks) -> OAuthCredential:
    state = secrets.token_urlsafe(32)
    url = _build_authorization_url(state)

    server, code_future = await _start_local_server(state)
    callbacks.on_auth(OAuthAuthInfo(
        url=url,
        instructions=(
            "Complete Google login in your browser. "
            "If the browser is on another machine, paste the final redirect URL here."
        ),
    ))

    # Race the browser callback against optional manual paste. The manual
    # task MUST be cancelable (see _read_line_cancelable) — cancelling it
    # tears down the stdin reader so nothing keeps consuming stdin after we
    # return. We await the cancelled tasks so that teardown completes before
    # control returns to the REPL.
    code: Optional[str] = None
    recv_state: Optional[str] = None
    try:
        if callbacks.on_manual_code_input:
            browser_task = asyncio.ensure_future(code_future)
            manual_task = asyncio.ensure_future(callbacks.on_manual_code_input())
            done, pending = await asyncio.wait(
                [browser_task, manual_task],
                timeout=300,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

            if browser_task in done and not browser_task.cancelled() and browser_task.exception() is None:
                code = browser_task.result()
                recv_state = state
            elif manual_task in done and not manual_task.cancelled() and manual_task.exception() is None:
                raw = manual_task.result()
                parsed_code, parsed_state = _parse_authorization_input(raw)
                if parsed_state and parsed_state != state:
                    raise ValueError("OAuth state mismatch")
                code = parsed_code
                recv_state = parsed_state or state
        else:
            try:
                code = await asyncio.wait_for(asyncio.shield(code_future), timeout=300)
                recv_state = state
            except asyncio.TimeoutError:
                pass
    finally:
        server.close()
        await server.wait_closed()

    if not code:
        raw = await callbacks.on_prompt(OAuthPrompt(
            message="Paste the authorization code or full redirect URL:",
            placeholder=REDIRECT_URI,
        ))
        parsed_code, parsed_state = _parse_authorization_input(raw)
        if parsed_state and parsed_state != state:
            raise ValueError("OAuth state mismatch")
        code = parsed_code
        recv_state = parsed_state or state

    if not code:
        raise ValueError("Missing authorization code")
    if not recv_state:
        raise ValueError("Missing OAuth state")

    if callbacks.on_progress:
        callbacks.on_progress("Exchanging authorization code for tokens...")

    data = await asyncio.to_thread(_exchange_code, code, recv_state)
    access, refresh, expires_ms = _parse_token_response(data)

    if callbacks.on_progress:
        callbacks.on_progress("Setting up Cloud Code Assist access...")

    from program.inference.api.text.google_antigravity import fetch_project_id, onboard_user
    project_id = await fetch_project_id(access)
    await onboard_user(access, project_id)

    return OAuthCredential(access=access, refresh=refresh, expires=expires_ms)


async def refresh_antigravity_token(credential: OAuthCredential, signal: Optional[AbortSignal] = None) -> OAuthCredential:
    data = await asyncio.to_thread(_refresh_token_sync, credential.refresh)
    access, new_refresh, expires_ms = _parse_token_response(data)
    refresh = new_refresh or credential.refresh
    return OAuthCredential(access=access, refresh=refresh, expires=expires_ms)


@dataclass
class GoogleAntigravityOAuthProvider(OAuthProvider):
    id: str = "antigravity"
    name: str = "Google Antigravity"
    uses_callback_server: bool = True

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredential:
        return await login_antigravity(callbacks)

    async def refresh_token(self, credential: OAuthCredential, signal: Optional[AbortSignal] = None) -> OAuthCredential:
        return await refresh_antigravity_token(credential, signal=signal)

    async def logout(self, credential: OAuthCredential) -> None:
        # Revoke token via Google's revocation endpoint
        try:
            req = urllib.request.Request(
                f"https://oauth2.googleapis.com/revoke?token={urllib.parse.quote(credential.access)}",
                data=b"",
                method="POST",
            )
            with urllib.request.urlopen(req, context=_SSL_CONTEXT, timeout=10):
                pass
        except Exception:
            pass

    def get_api_key(self, credential: OAuthCredential) -> str:
        return credential.access

    @property
    def api(self):
        from program.inference.api.text.google_antigravity import GoogleAntigravityAPI
        return GoogleAntigravityAPI

    async def validate(self, credential: OAuthCredential, signal: Optional[AbortSignal] = None) -> bool:
        if self.is_expired(credential):
            return False
        if signal and signal.is_set():
            return False
        return await asyncio.to_thread(_validate_token_sync, credential.access)

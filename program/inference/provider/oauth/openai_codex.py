"""
OpenAI Codex (ChatGPT OAuth) flow — PKCE + local callback server.

The access token returned by OpenAI is used directly as a Bearer token
for calls to the OpenAI API, replacing a traditional API key.
"""
from __future__ import annotations

import asyncio
import base64
import json
import secrets
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

import certifi

_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

from dataclasses import dataclass
from program.inference.provider.types import OAuthProvider
from program.inference.provider.oauth.pkce import generate_pkce
from program.inference.provider.oauth.types import OAuthAuthInfo, OAuthCredential, OAuthLoginCallbacks, OAuthPrompt, AbortSignal

__all__ = ["OpenAICodexOAuthProvider"]

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
REVOKE_URL = "https://auth.openai.com/oauth/revoke"
USERINFO_URL = "https://auth.openai.com/oauth/userinfo"
REDIRECT_URI = "http://localhost:1455/auth/callback"
SCOPES = "openid profile email offline_access"
JWT_CLAIM_PATH = "https://api.openai.com/auth"
CALLBACK_HOST = None  # binds to all interfaces (IPv4 + IPv6)
CALLBACK_PORT = 1455

_SUCCESS_HTML = b"""<!DOCTYPE html><html><head><title>Auth complete</title></head><body>
<h2>Authentication successful!</h2>
<p>You can close this window and return to the application.</p>
</body></html>"""

_ERROR_HTML = b"""<!DOCTYPE html><html><head><title>Auth failed</title></head><body>
<h2>Authentication failed</h2>
<p>An error occurred. Please try again.</p>
</body></html>"""


def _create_state() -> str:
    return secrets.token_hex(16)


def _decode_jwt(token: str) -> dict | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        padding = (4 - len(payload) % 4) % 4
        decoded = base64.urlsafe_b64decode(payload + "=" * padding)
        return json.loads(decoded)
    except Exception:
        return None


def _get_account_id(access_token: str) -> str | None:
    payload = _decode_jwt(access_token)
    if not isinstance(payload, dict):
        return None
    auth = payload.get(JWT_CLAIM_PATH)
    if not isinstance(auth, dict):
        return None
    account_id = auth.get("chatgpt_account_id")
    return account_id if isinstance(account_id, str) and account_id else None


def _parse_authorization_input(value: str) -> tuple[Optional[str], Optional[str]]:
    """Parse code and state from a redirect URL, raw query string, or bare code."""
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
    if "#" in value:
        code, state = value.split("#", 1)
        return code or None, state or None
    if "code=" in value:
        params = urllib.parse.parse_qs(value)
        return params.get("code", [None])[0], params.get("state", [None])[0]
    return value, None


def _build_authorization_url(challenge: str, state: str, originator: str) -> str:
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": originator,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _post_token(body: dict[str, str]) -> dict:
    data = urllib.parse.urlencode(body).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CONTEXT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        raise RuntimeError(f"Token request failed ({e.code}): {body_text}") from e


def _exchange_code(code: str, verifier: str) -> dict:
    return _post_token({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    })


def _refresh_token_sync(refresh_token: str) -> dict:
    return _post_token({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
    })


def _revoke_token_sync(token: str) -> None:
    data = urllib.parse.urlencode({
        "token": token,
        "client_id": CLIENT_ID,
    }).encode()
    req = urllib.request.Request(
        REVOKE_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CONTEXT):
            pass
    except urllib.error.HTTPError:
        pass  # best-effort; treat any error as revocation attempt done


def _validate_token_sync(access_token: str) -> bool:
    req = urllib.request.Request(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CONTEXT) as resp:
            return resp.status == 200
    except urllib.error.HTTPError:
        return False
    except Exception:
        return False


def _parse_token_response(data: dict) -> tuple[str, str, int]:
    """Returns (access_token, refresh_token, expires_ms)."""
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    expires_in = data.get("expires_in")
    if not access or not refresh or not isinstance(expires_in, (int, float)):
        raise ValueError(f"Token response missing fields: {data}")
    expires_ms = int(time.time() * 1000) + int(expires_in) * 1000
    return access, refresh, expires_ms


async def _start_local_server(state: str) -> tuple[asyncio.Server, asyncio.Future[str]]:
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

            if parsed.path == "/auth/callback":
                recv_state = params.get("state", [None])[0]
                code = params.get("code", [None])[0]
                if recv_state == state and code:
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


async def login_openai_codex(
    callbacks: OAuthLoginCallbacks,
    originator: str = "program",
) -> OAuthCredential:
    verifier, challenge = generate_pkce()
    state = _create_state()
    url = _build_authorization_url(challenge, state, originator)

    server, code_future = await _start_local_server(state)
    callbacks.on_auth(OAuthAuthInfo(
        url=url,
        instructions="A browser window should open. Complete login to finish.",
    ))

    # Race the browser callback against optional manual paste. The manual
    # task MUST be cancelable (see _read_line_cancelable) — cancelling it
    # tears down the stdin reader so nothing keeps consuming stdin after we
    # return. We await the cancelled tasks so that teardown completes before
    # control returns to the REPL.
    code: Optional[str] = None
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
            elif manual_task in done and not manual_task.cancelled() and manual_task.exception() is None:
                raw = manual_task.result()
                parsed_code, parsed_state = _parse_authorization_input(raw)
                if parsed_state and parsed_state != state:
                    raise ValueError("State mismatch")
                code = parsed_code
        else:
            try:
                code = await asyncio.wait_for(asyncio.shield(code_future), timeout=300)
            except asyncio.TimeoutError:
                pass
    finally:
        server.close()
        await server.wait_closed()

    if not code:
        raw = await callbacks.on_prompt(OAuthPrompt(
            message="Paste the authorization code (or full redirect URL):",
        ))
        parsed_code, parsed_state = _parse_authorization_input(raw)
        if parsed_state and parsed_state != state:
            raise ValueError("State mismatch")
        code = parsed_code

    if not code:
        raise ValueError("Missing authorization code")

    if callbacks.on_progress:
        callbacks.on_progress("Exchanging authorization code for tokens...")

    data = await asyncio.to_thread(_exchange_code, code, verifier)
    access, refresh, expires_ms = _parse_token_response(data)

    account_id = _get_account_id(access)
    if not account_id:
        raise ValueError("missing chatgpt_account_id in token. Ensure you have a valid ChatGPT subscription.")

    return OAuthCredential(access=access, refresh=refresh, expires=expires_ms, account_id=account_id)


async def refresh_openai_codex_token(credential: OAuthCredential, signal: Optional[AbortSignal] = None) -> OAuthCredential:
    data = await asyncio.to_thread(_refresh_token_sync, credential.refresh)
    access, refresh, expires_ms = _parse_token_response(data)

    account_id = _get_account_id(access)
    if not account_id:
        raise ValueError("missing chatgpt_account_id in refreshed token. Ensure you have a valid ChatGPT subscription.")

    return OAuthCredential(access=access, refresh=refresh, expires=expires_ms, account_id=account_id)


@dataclass
class OpenAICodexOAuthProvider(OAuthProvider):
    id: str = "openai-codex"
    name: str = "ChatGPT Plus/Pro (Codex Subscription)"
    uses_callback_server: bool = True

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredential:
        return await login_openai_codex(callbacks)

    async def refresh_token(self, credential: OAuthCredential, signal: Optional[AbortSignal] = None) -> OAuthCredential:
        return await refresh_openai_codex_token(credential, signal=signal)

    async def logout(self, credential: OAuthCredential) -> None:
        await asyncio.to_thread(_revoke_token_sync, credential.refresh)

    def get_api_key(self, credential: OAuthCredential) -> str:
        return credential.access

    @property
    def api(self):
        from program.inference.api.text.openai_codex_responses import OpenAICodexResponsesAPI
        return OpenAICodexResponsesAPI

    async def validate(self, credential: OAuthCredential, signal: Optional[AbortSignal] = None) -> bool:
        if self.is_expired(credential):
            return False
        if signal and signal.is_set():
            return False
        return await asyncio.to_thread(_validate_token_sync, credential.access)

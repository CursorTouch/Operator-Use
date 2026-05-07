"""
Anthropic OAuth flow (Claude Pro/Max) — PKCE + local callback server.

The access token returned by Anthropic is used directly as a Bearer token
for calls to the Anthropic API, replacing a traditional API key.
"""
from __future__ import annotations

import asyncio
import base64
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

import certifi

from dataclasses import dataclass
from program.llm.provider.types import OAuthProvider
from program.llm.provider.oauth.pkce import generate_pkce
from program.llm.provider.oauth.types import OAuthAuthInfo, OAuthCredentials, OAuthLoginCallbacks, OAuthPrompt, AbortSignal

__all__ = ["AnthropicClaudeCodeOAuthProvider"]

_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

_RAW_CLIENT_ID = "OWQxYzI1MGEtZTYxYi00NGQ5LTg4ZWQtNTk0NGQxOTYyZjVl"
CLIENT_ID = base64.b64decode(_RAW_CLIENT_ID).decode()
AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 53692
CALLBACK_PATH = "/callback"
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"
SCOPES = "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"

_SUCCESS_HTML = b"""<!DOCTYPE html><html><head><title>Auth complete</title></head><body>
<h2>Authentication successful!</h2>
<p>You can close this window and return to the application.</p>
</body></html>"""

_ERROR_HTML = b"""<!DOCTYPE html><html><head><title>Auth failed</title></head><body>
<h2>Authentication failed</h2>
<p>An error occurred. Please try again.</p>
</body></html>"""


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


def _get_account_id(access_token: str) -> str:
    payload = _decode_jwt(access_token)
    if isinstance(payload, dict):
        for key in ("sub", "user_id", "account_id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


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
    if "#" in value:
        code, state = value.split("#", 1)
        return code or None, state or None
    if "code=" in value:
        params = urllib.parse.parse_qs(value)
        return params.get("code", [None])[0], params.get("state", [None])[0]
    return value, None


def _build_authorization_url(challenge: str, state: str) -> str:
    params = {
        "code": "true",
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _post_json(url: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CONTEXT, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        raise RuntimeError(f"Request failed ({e.code}): {body_text}") from e


def _exchange_code(code: str, state: str, verifier: str) -> dict:
    return _post_json(TOKEN_URL, {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "state": state,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    })


def _refresh_token_sync(refresh_token: str) -> dict:
    return _post_json(TOKEN_URL, {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
    })


def _validate_token_sync(access_token: str) -> bool:
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/models",
        headers={
            "Authorization": f"Bearer {access_token}",
            "anthropic-version": "2023-06-01",
        },
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
    if not access or not refresh or not isinstance(expires_in, (int, float)):
        raise ValueError(f"Token response missing fields: {data}")
    # 5-minute buffer to avoid using a token just about to expire
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

    server = await asyncio.start_server(_handle, CALLBACK_HOST, CALLBACK_PORT)
    return server, code_future


async def login_anthropic(callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
    verifier, challenge = generate_pkce()
    # The state is the verifier itself (matches the TS implementation)
    state = verifier
    url = _build_authorization_url(challenge, state)

    server, code_future = await _start_local_server(state)
    callbacks.on_auth(OAuthAuthInfo(
        url=url,
        instructions=(
            "Complete login in your browser. "
            "If the browser is on another machine, paste the final redirect URL here."
        ),
    ))

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

    data = await asyncio.to_thread(_exchange_code, code, recv_state, verifier)
    access, refresh, expires_ms = _parse_token_response(data)
    account_id = _get_account_id(access)

    return OAuthCredentials(access=access, refresh=refresh, expires=expires_ms, account_id=account_id)


async def refresh_anthropic_token(credentials: OAuthCredentials, signal: Optional[AbortSignal] = None) -> OAuthCredentials:
    data = await asyncio.to_thread(_refresh_token_sync, credentials.refresh)
    access, refresh, expires_ms = _parse_token_response(data)
    account_id = _get_account_id(access) or credentials.account_id
    return OAuthCredentials(access=access, refresh=refresh, expires=expires_ms, account_id=account_id)


@dataclass
class AnthropicClaudeCodeOAuthProvider(OAuthProvider):
    id: str = "anthropic-claude-code"
    name: str = "Anthropic (Claude Pro/Max)"
    uses_callback_server: bool = True

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials:
        return await login_anthropic(callbacks)

    async def refresh_token(self, credentials: OAuthCredentials, signal: Optional[AbortSignal] = None) -> OAuthCredentials:
        return await refresh_anthropic_token(credentials, signal=signal)

    async def logout(self, credentials: OAuthCredentials) -> None:
        # Anthropic OAuth does not expose a token revocation endpoint
        pass

    def get_api_key(self, credentials: OAuthCredentials) -> str:
        return credentials.access

    @property
    def api(self):
        from program.llm.api.anthropic_claude_code import AnthropicClaudeCodeAPI
        return AnthropicClaudeCodeAPI

    async def validate(self, credentials: OAuthCredentials, signal: Optional[AbortSignal] = None) -> bool:
        if self.is_expired(credentials):
            return False
        if signal and signal.is_set():
            return False
        return await asyncio.to_thread(_validate_token_sync, credentials.access)


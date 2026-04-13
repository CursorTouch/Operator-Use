"""
Claude Code OAuth provider.

Uses the OAuth token issued by the Claude Code CLI (`claude setup-token` or
via the Claude Code login flow) to access the Anthropic API with Bearer auth
instead of an API key.

Token resolution order:
  1. api_key argument (explicit token)
  2. CLAUDE_CODE_OAUTH_TOKEN environment variable
  3. ANTHROPIC_AUTH_TOKEN environment variable
  4. macOS Keychain (service: "Claude Code-credentials")
  5. ~/.claude/.credentials.json (file-based fallback)

Note: Token format is sk-ant-oat01-...
"""

import json
import logging
import os
import platform
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx

from anthropic import Anthropic, AsyncAnthropic

from operator_use.providers.anthropic.llm import ChatAnthropic

logger = logging.getLogger(__name__)

_KEYCHAIN_SERVICE = "Claude Code-credentials"
_CREDENTIALS_PATHS = [
    Path.home() / ".claude" / ".credentials.json",
    Path.home() / ".config" / "claude" / "credentials.json",
]


def _parse_claude_ai_oauth(data: dict) -> Optional[dict]:
    """Extract claudeAiOauth block from credentials file/keychain data."""
    block = data.get("claudeAiOauth")
    if not isinstance(block, dict):
        return None
    if not block.get("accessToken"):
        return None
    return block


def _load_keychain_token() -> Optional[dict]:
    """Read credentials from macOS Keychain (service: 'Claude Code-credentials')."""
    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout.strip())
            return _parse_claude_ai_oauth(data)
    except Exception as e:
        logger.debug(f"Keychain read failed: {e}")
    return None


def _load_file_credentials() -> Optional[dict]:
    """Read credentials from ~/.claude/.credentials.json."""
    for cred_path in _CREDENTIALS_PATHS:
        if cred_path.exists():
            try:
                data = json.loads(cred_path.read_text(encoding="utf-8"))
                block = _parse_claude_ai_oauth(data)
                if block:
                    logger.debug(f"Loaded Claude Code credentials from {cred_path}")
                    return block
            except Exception as e:
                logger.debug(f"Cannot read credentials from {cred_path}: {e}")
    return None


def load_claude_code_token() -> Optional[str]:
    """
    Load Claude Code OAuth token from any available source.

    Credential file format (~/.claude/.credentials.json):
      {"claudeAiOauth": {"accessToken": "sk-ant-oat01-...", "refreshToken": "...", "expiresAt": <ms>}}
    """
    # Explicit env vars take priority
    explicit = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if explicit:
        return explicit

    # macOS Keychain
    block = _load_keychain_token()
    if not block:
        block = _load_file_credentials()
    if not block:
        return None

    access = block.get("accessToken", "")
    expires_at = block.get("expiresAt", 0)  # milliseconds

    if expires_at and expires_at < (time.time() * 1000 + 60_000):
        refresh = block.get("refreshToken", "")
        if refresh:
            new_token = _refresh_claude_token(refresh)
            if new_token:
                return new_token

    return access or None


def _refresh_claude_token(refresh_token: str) -> Optional[str]:
    """
    Refresh Claude Code OAuth token.
    Uses Anthropic's OAuth token endpoint extracted from the Claude Code CLI OAuth flow.
    """
    try:
        # Claude Code uses the standard Anthropic OAuth endpoint
        r = httpx.post(
            "https://console.anthropic.com/v1/oauth/token",
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",  # Claude Code public client ID
            },
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        )
        if r.status_code == 200:
            data = r.json()
            return data.get("access_token")
    except Exception as e:
        logger.debug(f"Claude Code token refresh failed: {e}")
    return None


class ChatClaudeCode(ChatAnthropic):
    """
    Anthropic Claude provider using Claude Code OAuth (Bearer token auth).

    Identical to ChatAnthropic but authenticates via the OAuth token issued
    by the Claude Code CLI instead of an API key. Uses the Anthropic SDK's
    auth_token parameter which sends Authorization: Bearer {token}.

    To get a token: run `claude setup-token` and set CLAUDE_CODE_OAUTH_TOKEN,
    or simply log into Claude Code CLI and the token will be auto-discovered.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 600.0,
        max_retries: int = 2,
        temperature: Optional[float] = None,
        thinking_budget: Optional[int] = None,
        max_tokens: int = 4096,
        **kwargs,
    ):
        # Load token (api_key arg takes precedence for explicit override)
        token = api_key or load_claude_code_token()

        # Call grandparent __init__ to skip ChatAnthropic's api_key logic,
        # then set up clients with auth_token (Bearer) instead of api_key.
        # We replicate ChatAnthropic.__init__ but swap api_key → auth_token.
        super().__init__(
            model=model,
            api_key=None,  # don't pass api_key to parent
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            temperature=temperature,
            thinking_budget=thinking_budget,
            max_tokens=max_tokens,
            **kwargs,
        )

        # Override clients with auth_token-based auth
        self.client = Anthropic(
            auth_token=token,
            base_url=self.base_url,
            timeout=timeout,
            max_retries=max_retries,
        )
        self.aclient = AsyncAnthropic(
            auth_token=token,
            base_url=self.base_url,
            timeout=timeout,
            max_retries=max_retries,
        )

    @property
    def provider(self) -> str:
        return "claude_code"

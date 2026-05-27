"""Tests for OAuth provider logic and ProviderAuthManager with OAuth credentials."""
from __future__ import annotations

import json
import time
import base64
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from operator_use.auth.providers import ProviderAuthManager
from operator_use.auth.storage import InMemoryAuthStorage
from operator_use.auth.types import OAuthCredential, APICredential, AuthType
from operator_use.inference.provider.registry import TextProviderRegistry
from operator_use.inference.provider.oauth.anthropic_claude_code import (
    AnthropicClaudeCodeOAuthProvider,
    _parse_authorization_input as anthropic_parse_input,
    _parse_token_response as anthropic_parse_token,
    _build_authorization_url,
)
from operator_use.inference.provider.oauth.github_copilot import (
    GitHubCopilotOAuthProvider,
    get_copilot_base_url,
    normalize_domain,
)
from operator_use.inference.provider.oauth.openai_codex import (
    OpenAICodexOAuthProvider,
    _parse_authorization_input as openai_parse_input,
    _parse_token_response as openai_parse_token,
    _decode_jwt,
    _get_account_id,
)
from operator_use.inference.provider.oauth.google_antigravity import (
    GoogleAntigravityOAuthProvider,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def fresh_credential(access: str = "access-token", refresh: str = "refresh-token", account_id: str | None = None) -> OAuthCredential:
    """Credential that expires 1 hour from now."""
    return OAuthCredential(
        access=access,
        refresh=refresh,
        expires=int(time.time() * 1000) + 3_600_000,
        extra={"account_id": account_id} if account_id else {},
    )


def expired_credential(access: str = "old-access", refresh: str = "refresh-token") -> OAuthCredential:
    """Credential that expired 1 hour ago."""
    return OAuthCredential(
        access=access,
        refresh=refresh,
        expires=int(time.time() * 1000) - 3_600_000,
    )


def make_registry(*providers) -> TextProviderRegistry:
    registry = TextProviderRegistry()
    for p in providers:
        registry.register(p)
    return registry


def make_manager(registry: TextProviderRegistry, initial: dict = {}) -> ProviderAuthManager:
    return ProviderAuthManager.in_memory(registry, initial)


# ── OAuthProvider base: is_expired / ensure_fresh ─────────────────────────────

class TestOAuthProviderBase:
    def setup_method(self):
        self.provider = AnthropicClaudeCodeOAuthProvider()

    def test_fresh_credential_not_expired(self):
        cred = fresh_credential()
        assert self.provider.is_expired(cred) is False

    def test_expired_credential_is_expired(self):
        cred = expired_credential()
        assert self.provider.is_expired(cred) is True

    def test_credential_expiring_within_30s_considered_expired(self):
        cred = OAuthCredential(
            access="tok",
            refresh="ref",
            expires=int(time.time() * 1000) + 10_000,  # 10 seconds away
        )
        assert self.provider.is_expired(cred) is True

    @pytest.mark.asyncio
    async def test_ensure_fresh_returns_same_credential_when_not_expired(self):
        cred = fresh_credential()
        result = await self.provider.ensure_fresh(cred)
        assert result is cred

    @pytest.mark.asyncio
    async def test_ensure_fresh_refreshes_when_expired(self):
        cred = expired_credential()
        new_cred = fresh_credential(access="new-access")
        with patch.object(self.provider, "refresh_token", new=AsyncMock(return_value=new_cred)) as mock_refresh:
            result = await self.provider.ensure_fresh(cred)
        assert result.access == "new-access"
        mock_refresh.assert_called_once_with(credential=cred, signal=None)


# ── get_api_key on concrete providers ─────────────────────────────────────────

class TestProviderGetApiKey:
    def test_anthropic_returns_access_token(self):
        p = AnthropicClaudeCodeOAuthProvider()
        cred = fresh_credential(access="my-bearer")
        assert p.get_api_key(credential=cred) == "my-bearer"

    def test_github_copilot_returns_access_token(self):
        p = GitHubCopilotOAuthProvider()
        cred = fresh_credential(access="copilot-token")
        assert p.get_api_key(cred) == "copilot-token"

    def test_openai_codex_returns_access_token(self):
        p = OpenAICodexOAuthProvider()
        cred = fresh_credential(access="openai-token")
        assert p.get_api_key(credential=cred) == "openai-token"

    def test_google_antigravity_returns_access_token(self):
        p = GoogleAntigravityOAuthProvider()
        cred = fresh_credential(access="google-token")
        assert p.get_api_key(credential=cred) == "google-token"


# ── Anthropic pure helpers ─────────────────────────────────────────────────────

class TestAnthropicParseAuthorizationInput:
    def test_bare_code(self):
        code, state = anthropic_parse_input("mycode123")
        assert code == "mycode123"
        assert state is None

    def test_full_redirect_url(self):
        code, state = anthropic_parse_input(
            "http://localhost:53692/callback?code=abc&state=xyz"
        )
        assert code == "abc"
        assert state == "xyz"

    def test_hash_separated(self):
        code, state = anthropic_parse_input("mycode#mystate")
        assert code == "mycode"
        assert state == "mystate"

    def test_query_string(self):
        code, state = anthropic_parse_input("code=abc&state=xyz")
        assert code == "abc"
        assert state == "xyz"

    def test_empty_returns_none_none(self):
        assert anthropic_parse_input("") == (None, None)
        assert anthropic_parse_input("   ") == (None, None)


class TestAnthropicParseTokenResponse:
    def test_valid_response(self):
        data = {"access_token": "acc", "refresh_token": "ref", "expires_in": 3600}
        access, refresh, expires_ms = anthropic_parse_token(data)
        assert access == "acc"
        assert refresh == "ref"
        assert expires_ms > int(time.time() * 1000)

    def test_missing_field_raises(self):
        with pytest.raises(ValueError):
            anthropic_parse_token({"access_token": "acc"})

    def test_expiry_has_5_minute_buffer(self):
        now_ms = int(time.time() * 1000)
        data = {"access_token": "a", "refresh_token": "r", "expires_in": 3600}
        _, _, expires_ms = anthropic_parse_token(data)
        buffer = 5 * 60 * 1000
        assert expires_ms <= now_ms + 3600 * 1000 - buffer + 1000


class TestAnthropicBuildAuthorizationUrl:
    def test_contains_required_params(self):
        url = _build_authorization_url("challenge123", "state456")
        assert "code_challenge=challenge123" in url
        assert "state=state456" in url
        assert "code_challenge_method=S256" in url
        assert "response_type=code" in url


# ── GitHub Copilot pure helpers ────────────────────────────────────────────────

class TestNormalizeDomain:
    def test_bare_domain(self):
        assert normalize_domain("company.ghe.com") == "company.ghe.com"

    def test_with_https_scheme(self):
        assert normalize_domain("https://company.ghe.com") == "company.ghe.com"

    def test_with_trailing_slash(self):
        assert normalize_domain("company.ghe.com/") == "company.ghe.com"

    def test_empty_string_returns_none(self):
        assert normalize_domain("") is None

    def test_whitespace_returns_none(self):
        assert normalize_domain("   ") is None


class TestGetCopilotBaseUrl:
    def test_default_url_when_no_token(self):
        url = get_copilot_base_url()
        assert url == "https://api.individual.githubcopilot.com"

    def test_enterprise_domain_used_when_no_token(self):
        url = get_copilot_base_url(enterprise_domain="company.ghe.com")
        assert url == "https://copilot-api.company.ghe.com"

    def test_proxy_ep_extracted_from_token(self):
        token = "tid=xxx;proxy-ep=proxy.example.com;other=val"
        url = get_copilot_base_url(token=token)
        assert url == "https://api.example.com"

    def test_token_takes_precedence_over_enterprise_domain(self):
        token = "proxy-ep=proxy.from-token.com"
        url = get_copilot_base_url(token=token, enterprise_domain="ignored.com")
        assert "from-token" in url


# ── OpenAI Codex pure helpers ──────────────────────────────────────────────────

class TestOpenAIParseAuthorizationInput:
    def test_bare_code(self):
        code, state = openai_parse_input("mycode")
        assert code == "mycode"
        assert state is None

    def test_full_redirect_url(self):
        code, state = openai_parse_input(
            "http://localhost:1455/auth/callback?code=abc&state=xyz"
        )
        assert code == "abc"
        assert state == "xyz"

    def test_empty_returns_none_none(self):
        assert openai_parse_input("") == (None, None)


class TestDecodeJwt:
    def _make_jwt(self, payload: dict) -> str:
        header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        return f"{header}.{body}.fakesig"

    def test_decodes_payload(self):
        jwt = self._make_jwt({"sub": "user123"})
        result = _decode_jwt(jwt)
        assert result["sub"] == "user123"

    def test_invalid_jwt_returns_none(self):
        assert _decode_jwt("notajwt") is None
        assert _decode_jwt("a.b") is None


class TestGetAccountId:
    def _make_jwt(self, account_id: str | None) -> str:
        claim = {"https://api.openai.com/auth": {"chatgpt_account_id": account_id}} if account_id else {}
        payload = base64.urlsafe_b64encode(json.dumps(claim).encode()).rstrip(b"=").decode()
        header = base64.urlsafe_b64encode(b"{}").rstrip(b"=").decode()
        return f"{header}.{payload}.sig"

    def test_extracts_account_id(self):
        jwt = self._make_jwt("acct_abc123")
        assert _get_account_id(jwt) == "acct_abc123"

    def test_missing_claim_returns_none(self):
        jwt = self._make_jwt(None)
        assert _get_account_id(jwt) is None


class TestOpenAIParseTokenResponse:
    def test_valid_response(self):
        data = {"access_token": "acc", "refresh_token": "ref", "expires_in": 3600}
        access, refresh, expires_ms = openai_parse_token(data)
        assert access == "acc"
        assert refresh == "ref"
        assert expires_ms > int(time.time() * 1000)

    def test_missing_field_raises(self):
        with pytest.raises(ValueError):
            openai_parse_token({"access_token": "only"})


# ── ProviderAuthManager with OAuth credentials ────────────────────────────────

class TestProviderAuthManagerOAuth:
    def setup_method(self):
        self.provider = AnthropicClaudeCodeOAuthProvider()
        self.registry = make_registry(self.provider)

    def test_get_returns_stored_oauth_credential(self):
        cred = fresh_credential()
        manager = make_manager(self.registry)
        manager.set("anthropic-claude-code", cred)
        assert manager.get("anthropic-claude-code") is cred

    def test_has_returns_true_when_credential_stored(self):
        manager = make_manager(self.registry)
        manager.set("anthropic-claude-code", fresh_credential())
        assert manager.has("anthropic-claude-code") is True

    def test_has_returns_false_when_not_stored(self):
        manager = make_manager(self.registry)
        assert manager.has("anthropic-claude-code") is False

    def test_remove_deletes_credential(self):
        manager = make_manager(self.registry)
        manager.set("anthropic-claude-code", fresh_credential())
        manager.remove("anthropic-claude-code")
        assert manager.has("anthropic-claude-code") is False

    @pytest.mark.asyncio
    async def test_get_api_key_returns_access_token_for_fresh_credential(self):
        cred = fresh_credential(access="bearer-xyz")
        manager = make_manager(self.registry)
        manager.set("anthropic-claude-code", cred)
        key = await manager.get_api_key("anthropic-claude-code")
        assert key == "bearer-xyz"

    @pytest.mark.asyncio
    async def test_get_api_key_refreshes_expired_credential(self):
        cred = expired_credential()
        new_cred = fresh_credential(access="refreshed-token")
        with patch.object(self.provider, "refresh_token", new=AsyncMock(return_value=new_cred)):
            manager = make_manager(self.registry)
            manager.set("anthropic-claude-code", cred)
            key = await manager.get_api_key("anthropic-claude-code")
        assert key == "refreshed-token"

    @pytest.mark.asyncio
    async def test_get_api_key_returns_none_when_refresh_fails(self):
        cred = expired_credential()
        with patch.object(self.provider, "refresh_token", new=AsyncMock(side_effect=RuntimeError("refresh failed"))):
            manager = make_manager(self.registry)
            manager.set("anthropic-claude-code", cred)
            key = await manager.get_api_key("anthropic-claude-code")
        assert key is None

    @pytest.mark.asyncio
    async def test_get_api_key_falls_back_to_env_var(self, monkeypatch):
        # provider id uppercased: ANTHROPIC-CLAUDE-CODE_API_KEY
        monkeypatch.setenv("ANTHROPIC-CLAUDE-CODE_API_KEY", "env-key")
        manager = make_manager(self.registry)
        key = await manager.get_api_key("anthropic-claude-code")
        assert key == "env-key"

    @pytest.mark.asyncio
    async def test_get_api_key_runtime_override_takes_precedence(self):
        cred = fresh_credential(access="stored-token")
        manager = make_manager(self.registry)
        manager.set("anthropic-claude-code", cred)
        manager.set_runtime_api_key("anthropic-claude-code", "override-token")
        key = await manager.get_api_key("anthropic-claude-code")
        assert key == "override-token"

    def test_get_auth_status_stored(self):
        manager = make_manager(self.registry)
        manager.set("anthropic-claude-code", fresh_credential())
        status = manager.get_auth_status("anthropic-claude-code")
        assert status.configured is True
        assert status.source == "stored"

    def test_get_auth_status_runtime(self):
        manager = make_manager(self.registry)
        manager.set_runtime_api_key("anthropic-claude-code", "rt-key")
        status = manager.get_auth_status("anthropic-claude-code")
        assert status.configured is True
        assert status.source == "runtime"

    def test_get_auth_status_not_configured(self):
        manager = make_manager(self.registry)
        status = manager.get_auth_status("anthropic-claude-code")
        assert status.configured is False

    @pytest.mark.asyncio
    async def test_login_stores_credential(self):
        new_cred = fresh_credential(access="login-token")
        callbacks = MagicMock()
        with patch.object(self.provider, "login", new=AsyncMock(return_value=new_cred)):
            manager = make_manager(self.registry)
            await manager.login("anthropic-claude-code", callbacks)

        stored = manager.get("anthropic-claude-code")
        assert isinstance(stored, OAuthCredential)
        assert stored.access == "login-token"

    @pytest.mark.asyncio
    async def test_logout_removes_credential(self):
        cred = fresh_credential()
        with patch.object(self.provider, "logout", new=AsyncMock()):
            manager = make_manager(self.registry)
            manager.set("anthropic-claude-code", cred)
            await manager.logout("anthropic-claude-code")

        assert manager.has("anthropic-claude-code") is False


# ── ProviderAuthManager persistence (InMemoryStorage round-trip) ──────────────

class TestProviderAuthManagerPersistence:
    def test_credential_persisted_and_reloadable(self):
        registry = make_registry(AnthropicClaudeCodeOAuthProvider())
        storage = InMemoryAuthStorage()
        manager = ProviderAuthManager.from_storage(registry, storage)

        cred = fresh_credential(access="persisted-token")
        manager.set("anthropic-claude-code", cred)

        # New manager instance from same storage
        manager2 = ProviderAuthManager.from_storage(registry, storage)
        stored = manager2.get("anthropic-claude-code")
        assert isinstance(stored, OAuthCredential)
        assert stored.access == "persisted-token"

    def test_remove_is_persisted(self):
        registry = make_registry(AnthropicClaudeCodeOAuthProvider())
        storage = InMemoryAuthStorage()
        manager = ProviderAuthManager.from_storage(registry, storage)

        manager.set("anthropic-claude-code", fresh_credential())
        manager.remove("anthropic-claude-code")

        manager2 = ProviderAuthManager.from_storage(registry, storage)
        assert manager2.has("anthropic-claude-code") is False

    def test_list_returns_all_stored_providers(self):
        registry = make_registry(
            AnthropicClaudeCodeOAuthProvider(),
            GitHubCopilotOAuthProvider(),
        )
        manager = make_manager(registry)
        manager.set("anthropic-claude-code", fresh_credential())
        manager.set("github-copilot", fresh_credential())
        assert set(manager.list()) == {"anthropic-claude-code", "github-copilot"}

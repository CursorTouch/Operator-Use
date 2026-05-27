from __future__ import annotations

from dataclasses import dataclass

from click.testing import CliRunner

from operator_use.inference.provider.types import APIProvider
from operator_use.inference.types import LLMOptions
from operator_use.console.auth import auth


@dataclass
class _Status:
    configured: bool = False
    source: str = "stored"
    label: str | None = None


@dataclass
class _Provider:
    id: str
    name: str


class _Registry:
    def __init__(self):
        self.oauth = _Provider("openai-codex", "ChatGPT")
        self.api = APIProvider("openai", "OpenAI", api="openai_responses", options=LLMOptions())

    def get_oauth_providers(self):
        return [self.oauth]

    def get_api_providers(self):
        return [self.api]

    def get_oauth_provider(self, provider):
        return self.oauth if provider == self.oauth.id else None

    def get(self, provider):
        if provider == self.oauth.id:
            return self.oauth
        if provider == self.api.id:
            return self.api
        return None


class _AuthStore:
    def __init__(self):
        self.logged_in = set()
        self.saved = {}
        self.runtime = {}
        self.login_calls: list[str] = []
        self.logout_calls: list[str] = []

    def get_auth_status(self, provider):
        if provider in self.logged_in or provider in self.saved:
            return _Status(configured=True, source="stored")
        if provider in self.runtime:
            return _Status(configured=True, source="runtime", label="--api-key")
        return _Status(configured=False)

    def has(self, provider):
        return provider in self.logged_in or provider in self.saved

    def set(self, provider, credential):
        self.saved[provider] = credential

    def remove(self, provider):
        self.saved.pop(provider, None)

    def set_runtime_api_key(self, provider, api_key):
        self.runtime[provider] = api_key

    def remove_runtime_api_key(self, provider):
        self.runtime.pop(provider, None)

    async def login(self, provider, callbacks):
        self.login_calls.append(provider)
        self.logged_in.add(provider)

    async def logout(self, provider):
        self.logout_calls.append(provider)
        self.logged_in.discard(provider)


def _patch_llm(monkeypatch, auth_store, registry):
    from operator_use.inference.api.text import service

    class _LLM:
        _auth_store = auth_store
        _providers = registry

    monkeypatch.setattr(service, "LLM", _LLM)


def test_auth_login_oauth_provider(monkeypatch):
    auth_store = _AuthStore()
    registry = _Registry()
    _patch_llm(monkeypatch, auth_store, registry)

    result = CliRunner().invoke(auth, ["login", "openai-codex"])

    assert result.exit_code == 0
    assert auth_store.login_calls == ["openai-codex"]
    assert "Logged in to ChatGPT." in result.output


def test_auth_logout_oauth_provider(monkeypatch):
    auth_store = _AuthStore()
    auth_store.logged_in.add("openai-codex")
    registry = _Registry()
    _patch_llm(monkeypatch, auth_store, registry)

    result = CliRunner().invoke(auth, ["logout", "openai-codex"])

    assert result.exit_code == 0
    assert auth_store.logout_calls == ["openai-codex"]
    assert "Logged out from ChatGPT." in result.output


def test_auth_login_unknown_oauth_provider(monkeypatch):
    auth_store = _AuthStore()
    registry = _Registry()
    _patch_llm(monkeypatch, auth_store, registry)

    result = CliRunner().invoke(auth, ["login", "anthropic"])

    assert result.exit_code == 0
    assert auth_store.login_calls == []
    assert "Unknown OAuth provider: 'anthropic'." in result.output


def test_provider_api_key_without_set_uses_runtime_override(monkeypatch):
    auth_store = _AuthStore()
    registry = _Registry()
    _patch_llm(monkeypatch, auth_store, registry)

    result = CliRunner().invoke(auth, ["openai", "--api-key", "sk-runtime"])

    assert result.exit_code == 0
    assert auth_store.runtime == {"openai": "sk-runtime"}
    assert auth_store.saved == {}
    assert "Runtime API key override set for OpenAI (openai)." in result.output


def test_provider_set_api_key_saves(monkeypatch):
    auth_store = _AuthStore()
    registry = _Registry()
    _patch_llm(monkeypatch, auth_store, registry)

    result = CliRunner().invoke(auth, ["openai", "set", "--api-key", "sk-saved"])

    assert result.exit_code == 0
    assert auth_store.saved["openai"].key == "sk-saved"
    assert auth_store.runtime == {}
    assert "API key saved for OpenAI (openai)." in result.output


def test_provider_unset_clears_saved_and_runtime_api_keys(monkeypatch):
    auth_store = _AuthStore()
    auth_store.saved["openai"] = object()
    auth_store.runtime["openai"] = "sk-runtime"
    registry = _Registry()
    _patch_llm(monkeypatch, auth_store, registry)

    result = CliRunner().invoke(auth, ["openai", "unset"])

    assert result.exit_code == 0
    assert auth_store.saved == {}
    assert auth_store.runtime == {}
    assert "API key cleared for OpenAI (openai)." in result.output

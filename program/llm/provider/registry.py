from __future__ import annotations
from program.llm.provider.types import APIProvider, OAuthProvider, AuthType

Provider = APIProvider | OAuthProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        self._providers[provider.id] = provider

    def unregister(self, provider_id: str) -> None:
        self._providers.pop(provider_id, None)

    def list(self) -> list[Provider]:
        return list(self._providers.values())

    def get(self, provider_id: str) -> Provider | None:
        return self._providers.get(provider_id)

    def is_using_oauth(self, provider: str) -> bool:
        if p := self.get(provider):
            return p.auth_type == AuthType.OAUTH
        raise ValueError(f"Provider '{provider}' not found.")

    def get_oauth_providers(self) -> list[OAuthProvider]:
        return [p for p in self._providers.values() if isinstance(p, OAuthProvider)]

    def get_api_providers(self) -> list[APIProvider]:
        return [p for p in self._providers.values() if isinstance(p, APIProvider)]

    def get_oauth_provider(self, provider: str) -> OAuthProvider | None:
        p = self.get(provider)
        return p if isinstance(p, OAuthProvider) else None

    def get_api_provider(self, provider: str) -> APIProvider | None:
        p = self.get(provider)
        return p if isinstance(p, APIProvider) else None

    def reset(self) -> None:
        self._providers.clear()

    @classmethod
    def from_builtins(cls) -> ProviderRegistry:
        from program.llm.provider.builtins import PROVIDERS
        instance = cls()
        for provider in PROVIDERS:
            instance.register(provider)
        return instance

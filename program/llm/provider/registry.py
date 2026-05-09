from __future__ import annotations
from program.llm.provider.types import APIProvider, OAuthProvider, AuthType

Provider = APIProvider | OAuthProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        key = provider.id if isinstance(provider, OAuthProvider) else provider.name
        self._providers[key] = provider

    def is_using_oauth(self, name: str) -> bool:
        if provider := self.get(name):
            return provider.auth_type == AuthType.OAUTH
        raise ValueError(f"Provider '{name}' not found.")

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)

    def get_api_key(self):
        pass

    def list(self) -> list[Provider]:
        return list(self._providers.values())

    def get(self, name: str) -> Provider | None:
        return self._providers.get(name)

    def reset(self) -> None:
        self._providers.clear()

    @classmethod
    def from_builtins(cls) -> ProviderRegistry:
        from program.llm.provider.builtins import PROVIDERS
        instance = cls()
        for provider in PROVIDERS:
            instance.register(provider)
        return instance

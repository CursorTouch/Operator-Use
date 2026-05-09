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
    
    def get_oauth_providers(self) -> list[OAuthProvider]:
        return [p for p in self._providers.values() if p.auth_type==AuthType.OAUTH]
    
    def get_api_providers(self) -> list[APIProvider]:
        return [p for p in self._providers.values() if p.auth_type==AuthType.API_KEY]

    def get_oauth_provider(self,provider:str)->OAuthProvider | None:
        provider=self.get(provider)
        if provider is None:
            return None
        return provider if provider.auth_type==AuthType.OAUTH else None
    
    def get_api_provider(self,provider:str) -> APIProvider | None:
        provider=self.get(provider)
        if provider is None:
            return None
        return provider if provider.auth_type==AuthType.API_KEY else None

    def reset(self) -> None:
        self._providers.clear()

    @classmethod
    def from_builtins(cls) -> ProviderRegistry:
        from program.llm.provider.builtins import PROVIDERS
        instance = cls()
        for provider in PROVIDERS:
            instance.register(provider)
        return instance

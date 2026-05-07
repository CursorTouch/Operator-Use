from __future__ import annotations
from program.llm.provider.types import APIProvider, OAuthProvider

Provider = APIProvider | OAuthProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        self._providers[provider.name] = provider

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)

    def list(self) -> list[Provider]:
        return list(self._providers.values())

    def get(self, name: str) -> Provider | None:
        return self._providers.get(name)

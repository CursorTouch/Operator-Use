from __future__ import annotations

from program.image.provider.types import Provider


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

    def reset(self) -> None:
        self._providers.clear()

    @classmethod
    def from_builtins(cls) -> ProviderRegistry:
        from program.image.provider.builtins import PROVIDERS
        instance = cls()
        for provider in PROVIDERS:
            instance.register(provider)
        return instance

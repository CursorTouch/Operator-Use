from __future__ import annotations

from operator_use.memory.provider.types import MemoryProvider


class MemoryProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, MemoryProvider] = {}

    def register(self, provider: MemoryProvider) -> None:
        self._providers[provider.id] = provider

    def unregister(self, provider_id: str) -> None:
        self._providers.pop(provider_id, None)

    def list(self) -> list[MemoryProvider]:
        return list(self._providers.values())

    def get(self, provider_id: str) -> MemoryProvider | None:
        return self._providers.get(provider_id)

    def reset(self) -> None:
        self._providers.clear()

    @classmethod
    def from_builtins(cls) -> MemoryProviderRegistry:
        from operator_use.builtins.providers.memory import providers

        instance = cls()
        for provider in providers:
            instance.register(provider)
        return instance

from __future__ import annotations
from operator_use.inference.provider.types import APIProvider, OAuthProvider, ImageProvider, AudioProvider, VideoProvider, AuthType

TextProvider = APIProvider | OAuthProvider


class TextProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, TextProvider] = {}

    def register(self, provider: TextProvider) -> None:
        self._providers[provider.id] = provider

    def unregister(self, provider_id: str) -> None:
        self._providers.pop(provider_id, None)

    def list(self) -> list[TextProvider]:
        return list(self._providers.values())

    def get(self, provider_id: str) -> TextProvider | None:
        return self._providers.get(provider_id)

    def is_using_oauth(self, provider: str) -> bool:
        if p := self.get(provider):
            return p.auth_type == AuthType.OAuth
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
    def from_builtins(cls) -> TextProviderRegistry:
        from operator_use.builtins.providers.text import providers
        instance = cls()
        for provider in providers:
            instance.register(provider)
        return instance

class ImageProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ImageProvider] = {}

    def register(self, provider: ImageProvider) -> None:
        self._providers[provider.name] = provider

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)

    def list(self) -> list[ImageProvider]:
        return list(self._providers.values())

    def get(self, name: str) -> ImageProvider | None:
        return self._providers.get(name)

    def reset(self) -> None:
        self._providers.clear()

    @classmethod
    def from_builtins(cls) -> ImageProviderRegistry:
        from operator_use.builtins.providers.image import providers
        instance = cls()
        for provider in providers:
            instance.register(provider)
        return instance


class AudioProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, AudioProvider] = {}

    def register(self, provider: AudioProvider) -> None:
        self._providers[provider.name] = provider

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)

    def list(self) -> list[AudioProvider]:
        return list(self._providers.values())

    def get(self, name: str) -> AudioProvider | None:
        return self._providers.get(name)

    def reset(self) -> None:
        self._providers.clear()

    @classmethod
    def from_builtins(cls) -> AudioProviderRegistry:
        from operator_use.builtins.providers.audio import providers
        instance = cls()
        for provider in providers:
            instance.register(provider)
        return instance


class VideoProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, VideoProvider] = {}

    def register(self, provider: VideoProvider) -> None:
        self._providers[provider.name] = provider

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)

    def list(self) -> list[VideoProvider]:
        return list(self._providers.values())

    def get(self, name: str) -> VideoProvider | None:
        return self._providers.get(name)

    def reset(self) -> None:
        self._providers.clear()

    @classmethod
    def from_builtins(cls) -> VideoProviderRegistry:
        from operator_use.builtins.providers.video import providers
        instance = cls()
        for provider in providers:
            instance.register(provider)
        return instance

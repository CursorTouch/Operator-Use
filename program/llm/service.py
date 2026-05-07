from __future__ import annotations
from collections.abc import AsyncIterator
from dataclasses import fields
from program.llm.model.registry import ModelRegistry
from program.llm.api.registry import APIRegistry
from program.llm.provider.registry import ProviderRegistry
from program.llm.provider.types import APIProvider, OAuthProvider
from program.llm.provider.oauth.store import load_credentials, save_credentials
from program.llm.types import LLMEvent, Options
from program.message.types import BaseMessage


class LLM:
    _apis = APIRegistry.from_builtins()
    _models = ModelRegistry.from_builtins()
    _providers = ProviderRegistry.from_builtins()

    def __init__(
        self,
        model_id: str,
        provider: str,
        options: Options | None = None,
    ) -> None:
        model = self._models.get(model_id)
        if model is None:
            raise ValueError(f"Model '{model_id}' not found.")

        resolved_provider = self._providers.get(provider)
        if resolved_provider is None:
            raise ValueError(f"Provider '{provider}' not found.")

        self.model = model

        if isinstance(resolved_provider, OAuthProvider):
            credentials = load_credentials(resolved_provider.id)
            if credentials is None:
                raise RuntimeError(
                    f"No credentials found for '{provider}'. "
                    f"Please log in first."
                )
            api_class = resolved_provider.api
            merged = self._merge_options(
                Options(api_key=resolved_provider.get_api_key(credentials)),
                options,
            )
            self._oauth_provider = resolved_provider
            self._credentials = credentials
            self.api = api_class(merged)
        else:
            merged = self._merge_options(resolved_provider.options, options)
            api_class = resolved_provider.api
            if isinstance(api_class, str):
                api_class = self._apis.get(api_class)
                if api_class is None:
                    raise ValueError(f"API '{resolved_provider.api}' not found in registry.")
            self._oauth_provider = None
            self._credentials = None
            self.api = api_class(merged)

    def _merge_options(self, base: Options, override: Options | None) -> Options:
        if override is None:
            return base
        merged = Options(**{f.name: getattr(base, f.name) for f in fields(base)})
        for f in fields(override):
            value = getattr(override, f.name)
            if value is not None:
                setattr(merged, f.name, value)
        return merged

    async def _refresh_if_needed(self) -> None:
        if self._oauth_provider is None or self._credentials is None:
            return
        if self._oauth_provider.is_expired(self._credentials):
            self._credentials = await self._oauth_provider.refresh_token(
                self._credentials, signal=self.api.options.signal
            )
            save_credentials(self._oauth_provider.id, self._credentials)
            api_key = self._oauth_provider.get_api_key(self._credentials)
            self.api.options.api_key = api_key

    async def stream(self, messages: list[BaseMessage]) -> AsyncIterator[LLMEvent]:
        await self._refresh_if_needed()
        async for event in self.api.stream(messages, model=self.model.id):
            yield event

    async def invoke(self, messages: list[BaseMessage]) -> list[LLMEvent]:
        await self._refresh_if_needed()
        return await self.api.invoke(messages, model=self.model.id)



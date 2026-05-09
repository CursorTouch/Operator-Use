from __future__ import annotations
from collections.abc import AsyncIterator
from dataclasses import fields
from program.llm.model.registry import ModelRegistry
from program.llm.api.registry import APIRegistry
from program.llm.provider.registry import ProviderRegistry
from program.llm.provider.types import APIProvider, OAuthProvider
from program.auth.service import AuthStore
from program.auth.types import OAuthCredential
from program.llm.types import LLMEvent, Options
from program.message.types import BaseMessage
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from program.tool.types import Tool


class LLM:
    _apis = APIRegistry.from_builtins()
    _models = ModelRegistry.from_builtins()
    _providers = ProviderRegistry.from_builtins()
    _auth_store = AuthStore(_providers)

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
            credential = self._auth_store.get(resolved_provider.id)
            if not isinstance(credential, OAuthCredential):
                raise RuntimeError(
                    f"No credentials found for '{provider}'. "
                    f"Please log in first."
                )
            api_class = resolved_provider.api
            # Synchronous init (will be updated correctly before first request)
            merged = self._merge_options(
                Options(api_key=resolved_provider.get_api_key(credential)),
                options,
            )
            self.provider_id = resolved_provider.id
            self.api = api_class(merged)
        else:
            merged = self._merge_options(resolved_provider.options, options)
            api_class = resolved_provider.api
            if isinstance(api_class, str):
                api_class = self._apis.get(api_class)
                if api_class is None:
                    raise ValueError(f"API '{resolved_provider.api}' not found in registry.")
            self.provider_id = resolved_provider.id
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

    async def stream(self, messages: list[BaseMessage], tools: Optional[list[Tool]] = None) -> AsyncIterator[LLMEvent]:
        api_key = await self._auth_store.get_api_key(self.provider_id)
        if api_key:
            self.api.options.api_key = api_key

        try:
            async for event in self.api.stream(messages, model=self.model.id, tools=tools):
                yield event
        except Exception as e:
            from program.llm.types import ErrorEvent, StopReason
            yield ErrorEvent(reason=StopReason.Error, error=str(e))

    async def invoke(self, messages: list[BaseMessage], tools: Optional[list[Tool]] = None) -> list[LLMEvent]:
        api_key = await self._auth_store.get_api_key(self.provider_id)
        if api_key:
            self.api.options.api_key = api_key

        try:
            return await self.api.invoke(messages, model=self.model.id, tools=tools)
        except Exception as e:
            from program.llm.types import ErrorEvent, StopReason
            return [ErrorEvent(reason=StopReason.Error, error=str(e))]



from __future__ import annotations
from collections.abc import AsyncIterator
from dataclasses import fields
from program.llm.model.registry import ModelRegistry
from program.llm.api.registry import APIRegistry
from program.llm.provider.registry import ProviderRegistry
from program.llm.provider.types import APIProvider, OAuthProvider
from program.auth.manager import AuthManager
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
    _auth_store = AuthManager(_providers)

    def __init__(
        self,
        model_id: str,
        provider: str | None = None,
        options: Options | None = None,
    ) -> None:
        model = self._models.get(model_id)
        if model is None:
            raise ValueError(f"Model '{model_id}' not found.")

        provider = provider or model.provider
        resolved_provider = self._providers.get(provider)
        if resolved_provider is None:
            raise ValueError(f"Provider '{provider}' not found.")

        self.model = model
        
        # 1. Resolve the API class
        # If the model explicitly overrides the API, use it. Otherwise use provider's API.
        api_name_or_class = model.api if getattr(model, "api", None) else resolved_provider.api
        api_class = api_name_or_class
        if isinstance(api_class, str):
            api_class = self._apis.get(api_class)
            if api_class is None:
                raise ValueError(f"API '{api_name_or_class}' not found in registry.")

        # 2. Resolve Base URL
        # If the model explicitly overrides the base_url, use it.
        base_url_override = model.base_url if getattr(model, "base_url", None) else None

        if isinstance(resolved_provider, OAuthProvider):
            credential = self._auth_store.get(resolved_provider.id)
            if not isinstance(credential, OAuthCredential):
                raise RuntimeError(
                    f"No credentials found for '{provider}'. "
                    f"Please log in first."
                )
            
            # Synchronous init (will be updated correctly before first request)
            base_opts = Options(api_key=resolved_provider.get_api_key(credential))
            if base_url_override:
                base_opts.base_url = base_url_override
                
            merged = self._merge_options(base_opts, options)
            self.provider_id = resolved_provider.id
            self.api = api_class(merged)
        else:
            base_opts = resolved_provider.options
            if base_url_override:
                # We don't mutate the provider's options, we merge over it
                override_opts = Options(base_url=base_url_override)
                base_opts = self._merge_options(base_opts, override_opts)
                
            merged = self._merge_options(base_opts, options)
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



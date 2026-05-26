from __future__ import annotations
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import fields
from program.inference.model.registry import ModelRegistry
from program.inference.api.text.registry import LLMAPIRegistry
from program.inference.provider.registry import TextProviderRegistry
from program.inference.provider.types import APIProvider, OAuthProvider
from program.auth.providers import ProviderAuthManager
from program.auth.types import OAuthCredential
from program.inference.types import LLMContext, LLMEvent, LLMOptions
from program.message.types import BaseMessage, SystemMessage
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from program.tool.types import Tool
    from program.inference.types import ThinkingLevel


class LLM:
    _apis = LLMAPIRegistry.from_builtins()
    _models = ModelRegistry.from_llm_builtins()
    _providers = TextProviderRegistry.from_builtins()
    _auth_store = ProviderAuthManager.create(_providers)

    def __init__(
        self,
        model_id: str,
        provider: str | None = None,
        options: LLMOptions | None = None,
        *,
        models: Optional[ModelRegistry] = None,
        providers: Optional[TextProviderRegistry] = None,
        apis: Optional[LLMAPIRegistry] = None,
        auth_store: Optional[ProviderAuthManager] = None,
    ) -> None:
        _models = models if models is not None else type(self)._models
        _providers = providers if providers is not None else type(self)._providers
        _apis = apis if apis is not None else type(self)._apis
        self._auth_store = auth_store if auth_store is not None else type(self)._auth_store

        model = _models.get(model_id, provider=provider)
        if model is None:
            raise ValueError(f"Model '{model_id}' not found.")

        provider = provider or model.provider
        resolved_provider = _providers.get(provider)
        if resolved_provider is None:
            raise ValueError(f"Provider '{provider}' not found.")

        self.model = model

        api_name_or_class = model.api or resolved_provider.api
        api_class = api_name_or_class
        if isinstance(api_class, str):
            api_class = _apis.get(api_class)
            if api_class is None:
                raise ValueError(f"API '{api_name_or_class}' not found in registry.")

        base_url_override = model.base_url

        if isinstance(resolved_provider, OAuthProvider):
            credential = self._auth_store.get(resolved_provider.id)
            if not isinstance(credential, OAuthCredential):
                raise RuntimeError(
                    f"No credentials found for '{provider}'. Please log in first."
                )
            base_opts = LLMOptions(api_key=resolved_provider.get_api_key(credential))
            if base_url_override:
                base_opts.base_url = base_url_override
            merged = self._merge_options(base_opts, options)
            self.provider_id = resolved_provider.id
            self.api = api_class(merged)
        else:
            base_opts = resolved_provider.options
            if base_url_override:
                override_opts = LLMOptions(base_url=base_url_override)
                base_opts = self._merge_options(base_opts, override_opts)
            merged = self._merge_options(base_opts, options)
            self.provider_id = resolved_provider.id
            self.api = api_class(merged)

        if self.api.options.max_tokens is None:
            self.api.options.max_tokens = model.max_tokens

    def _merge_options(self, base: LLMOptions, override: LLMOptions | None) -> LLMOptions:
        if override is None:
            return base
        merged = LLMOptions(**{f.name: getattr(base, f.name) for f in fields(base)})
        for f in fields(override):
            value = getattr(override, f.name)
            if value is not None:
                setattr(merged, f.name, value)
        return merged

    def _resolve_messages(self, context: LLMContext) -> list[BaseMessage]:
        messages = context.messages
        if context.system_prompt:
            if not messages or not isinstance(messages[0], SystemMessage):
                messages = [SystemMessage.text(context.system_prompt)] + messages
        return messages

    async def stream(self, context: LLMContext) -> AsyncGenerator[LLMEvent, None]:
        api_key = await self._auth_store.get_api_key(self.provider_id)
        if api_key:
            self.api.options.api_key = api_key

        messages = self._resolve_messages(context)
        api_context = LLMContext(
            messages=messages,
            tools=context.tools,
            response_format=context.response_format,
        )

        try:
            async for event in self.api.stream(api_context, model=self.model):
                yield event
        except Exception as e:
            from program.inference.types import ErrorEvent, StopReason
            yield ErrorEvent(reason=StopReason.Error, error=str(e))

    async def invoke(
        self,
        context: LLMContext,
        thinking_level: Optional["ThinkingLevel"] = None,
    ) -> list[LLMEvent]:
        from program.inference.types import ThinkingLevel
        api_key = await self._auth_store.get_api_key(self.provider_id)
        if api_key:
            self.api.options.api_key = api_key

        original = self.api.options.thinking_level
        if thinking_level is not None and thinking_level != ThinkingLevel.Off:
            self.api.options.thinking_level = thinking_level
        try:
            messages = self._resolve_messages(context)
            api_context = LLMContext(
                messages=messages,
                tools=context.tools,
                response_format=context.response_format,
            )
            return await self.api.invoke(api_context, model=self.model)
        except Exception as e:
            from program.inference.types import ErrorEvent, StopReason
            return [ErrorEvent(reason=StopReason.Error, error=str(e))]
        finally:
            self.api.options.thinking_level = original

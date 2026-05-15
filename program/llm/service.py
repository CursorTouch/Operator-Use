from __future__ import annotations
from collections.abc import AsyncIterator
from dataclasses import fields
from program.llm.model.registry import ModelRegistry
from program.llm.api.registry import APIRegistry
from program.llm.provider.registry import ProviderRegistry
from program.llm.provider.types import APIProvider, OAuthProvider
from program.auth.manager import AuthManager
from program.auth.types import OAuthCredential
from program.llm.types import LLMContext, LLMEvent, Options
from program.message.types import BaseMessage, SystemMessage
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from program.tool.types import Tool



class LLM:
    _apis = APIRegistry.from_builtins()
    _models = ModelRegistry.from_builtin()
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

        api_name_or_class = model.api if getattr(model, "api", None) else resolved_provider.api
        api_class = api_name_or_class
        if isinstance(api_class, str):
            api_class = self._apis.get(api_class)
            if api_class is None:
                raise ValueError(f"API '{api_name_or_class}' not found in registry.")

        base_url_override = model.base_url if getattr(model, "base_url", None) else None

        if isinstance(resolved_provider, OAuthProvider):
            credential = self._auth_store.get(resolved_provider.id)
            if not isinstance(credential, OAuthCredential):
                raise RuntimeError(
                    f"No credentials found for '{provider}'. Please log in first."
                )
            base_opts = Options(api_key=resolved_provider.get_api_key(credential))
            if base_url_override:
                base_opts.base_url = base_url_override
            merged = self._merge_options(base_opts, options)
            self.provider_id = resolved_provider.id
            self.api = api_class(merged)
        else:
            base_opts = resolved_provider.options
            if base_url_override:
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

    def _resolve_messages(self, context: LLMContext) -> list[BaseMessage]:
        """Prepend system prompt as SystemMessage if set and not already present."""
        messages = context.messages
        if context.system_prompt:
            if not messages or not isinstance(messages[0], SystemMessage):
                messages = [SystemMessage.text(context.system_prompt)] + messages
        return messages

    async def stream(self, context: LLMContext) -> AsyncIterator[LLMEvent]:
        api_key = await self._auth_store.get_api_key(self.provider_id)
        if api_key:
            self.api.options.api_key = api_key

        messages = self._resolve_messages(context)
        api_context = LLMContext(messages=messages, tools=context.tools)

        try:
            async for event in self.api.stream(api_context, model=self.model.id):
                yield event
        except Exception as e:
            from program.llm.types import ErrorEvent, StopReason
            yield ErrorEvent(reason=StopReason.Error, error=str(e))

    async def invoke(self, context: LLMContext) -> list[LLMEvent]:
        api_key = await self._auth_store.get_api_key(self.provider_id)
        if api_key:
            self.api.options.api_key = api_key

        messages = self._resolve_messages(context)
        api_context = LLMContext(messages=messages, tools=context.tools)

        try:
            return await self.api.invoke(api_context, model=self.model.id)
        except Exception as e:
            from program.llm.types import ErrorEvent, StopReason
            return [ErrorEvent(reason=StopReason.Error, error=str(e))]

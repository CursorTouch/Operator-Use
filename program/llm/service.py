from __future__ import annotations
from collections.abc import AsyncIterator
from dataclasses import fields
from program.llm.model.builtins import MODELS
from program.llm.model.registry import ModelRegistry
from program.llm.provider.builtins import PROVIDERS
from program.llm.provider.registry import ProviderRegistry
from program.llm.types import LLMEvent, Options
from program.message.types import BaseMessage


class LLM:
    _models = ModelRegistry()
    _providers = ProviderRegistry()

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
        merged = self._merge_options(resolved_provider.options, options)
        self.api = resolved_provider.api(merged)

    def _merge_options(self, base: Options, override: Options | None) -> Options:
        if override is None:
            return base
        merged = Options(**{f.name: getattr(base, f.name) for f in fields(base)})
        for f in fields(override):
            value = getattr(override, f.name)
            if value is not None:
                setattr(merged, f.name, value)
        return merged

    def stream(self, messages: list[BaseMessage]) -> AsyncIterator[LLMEvent]:
        return self.api.stream(messages, model=self.model.id)

    async def invoke(self, messages: list[BaseMessage]) -> list[LLMEvent]:
        return await self.api.invoke(messages, model=self.model.id)


for _provider in PROVIDERS:
    LLM._providers.register(_provider)

for _model in MODELS:
    LLM._models.register(_model)

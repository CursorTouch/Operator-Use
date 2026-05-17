from __future__ import annotations
from typing import Type
from program.inference.api.text.base import BaseLLMAPI


class LLMAPIRegistry:
    def __init__(self) -> None:
        self._apis: dict[str, Type[BaseLLMAPI]] = {}

    def register(self, name: str, api: Type[BaseLLMAPI]) -> None:
        self._apis[name] = api

    def unregister(self, name: str) -> None:
        self._apis.pop(name, None)

    def list(self) -> list[Type[BaseLLMAPI]]:
        return list(self._apis.values())

    def get(self, name: str) -> Type[BaseLLMAPI] | None:
        return self._apis.get(name)

    def reset(self) -> None:
        self._apis.clear()

    @classmethod
    def from_builtins(cls) -> LLMAPIRegistry:
        from program.inference.api.text.builtins import LLM_APIS
        instance = cls()
        for name, api in LLM_APIS:
            instance.register(name, api)
        return instance


# Backward-compat alias
APIRegistry = LLMAPIRegistry

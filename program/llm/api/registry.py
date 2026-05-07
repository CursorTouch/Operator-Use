from __future__ import annotations
from typing import Type
from program.llm.api.base import BaseAPI


class APIRegistry:
    def __init__(self) -> None:
        self._apis: dict[str, Type[BaseAPI]] = {}

    def register(self, name: str, api: Type[BaseAPI]) -> None:
        self._apis[name] = api

    def unregister(self, name: str) -> None:
        self._apis.pop(name, None)

    def list(self) -> list[Type[BaseAPI]]:
        return list(self._apis.values())

    def get(self, name: str) -> Type[BaseAPI] | None:
        return self._apis.get(name)

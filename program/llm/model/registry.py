from __future__ import annotations
from program.llm.model.types import Model


class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, Model] = {}

    def register(self, model: Model) -> None:
        self._models[model.id] = model

    def unregister(self, model_id: str) -> None:
        self._models.pop(model_id, None)

    def list(self) -> list[Model]:
        return list(self._models.values())

    def get(self, model_id: str) -> Model | None:
        return self._models.get(model_id)

    def reset(self) -> None:
        self._models.clear()

    @classmethod
    def from_builtins(cls) -> ModelRegistry:
        from program.llm.model.builtins import MODELS
        instance = cls()
        for model in MODELS:
            instance.register(model)
        return instance

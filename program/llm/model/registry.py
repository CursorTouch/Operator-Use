from __future__ import annotations
from program.llm.model.types import Model


class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, list[Model]] = {}

    def register(self, model: Model) -> None:
        self._models.setdefault(model.id, []).append(model)

    def unregister(self, model_id: str, provider: str | None = None) -> None:
        if provider is None:
            self._models.pop(model_id, None)
        else:
            remaining = [m for m in self._models.get(model_id, []) if m.provider != provider]
            if remaining:
                self._models[model_id] = remaining
            else:
                self._models.pop(model_id, None)

    def list(self) -> list[Model]:
        return [m for models in self._models.values() for m in models]

    def get(self, model_id: str, provider: str | None = None) -> Model | None:
        models = self._models.get(model_id, [])
        if not models:
            return None
        if provider is None:
            return models[0]
        return next((m for m in models if m.provider == provider), None)

    def reset(self) -> None:
        self._models.clear()

    def load_builtin_models(self) -> None:
        from program.llm.model.builtins import MODELS
        for model in MODELS:
            self.register(model)

    @classmethod
    def from_builtin(cls) -> ModelRegistry:
        instance = cls()
        instance.load_builtin_models()
        return instance

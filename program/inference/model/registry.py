from __future__ import annotations
from program.inference.model.types import Model


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

    @classmethod
    def from_llm_builtins(cls) -> ModelRegistry:
        from program.inference.model.builtins import LLM_MODELS
        instance = cls()
        for model in LLM_MODELS:
            instance.register(model)
        return instance

    @classmethod
    def from_image_builtins(cls) -> ModelRegistry:
        from program.inference.model.builtins import IMAGE_MODELS
        instance = cls()
        for model in IMAGE_MODELS:
            instance.register(model)
        return instance

    @classmethod
    def from_all_builtins(cls) -> ModelRegistry:
        from program.inference.model.builtins import LLM_MODELS, IMAGE_MODELS
        instance = cls()
        for model in LLM_MODELS + IMAGE_MODELS:
            instance.register(model)
        return instance

    # Backward-compat aliases
    @classmethod
    def from_builtin(cls) -> ModelRegistry:
        return cls.from_llm_builtins()

    @classmethod
    def from_builtins(cls) -> ModelRegistry:
        return cls.from_image_builtins()

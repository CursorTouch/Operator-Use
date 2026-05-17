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
        from program.builtins.models.text import models
        instance = cls()
        for model in models:
            instance.register(model)
        return instance

    @classmethod
    def from_image_builtins(cls) -> ModelRegistry:
        from program.builtins.models.image import models
        instance = cls()
        for model in models:
            instance.register(model)
        return instance

    @classmethod
    def from_audio_builtins(cls) -> ModelRegistry:
        from program.builtins.models.audio import models
        instance = cls()
        for model in models:
            instance.register(model)
        return instance

    @classmethod
    def from_video_builtins(cls) -> ModelRegistry:
        from program.builtins.models.video import models
        instance = cls()
        for model in models:
            instance.register(model)
        return instance

    @classmethod
    def from_all_builtins(cls) -> ModelRegistry:
        from program.builtins.models.text import models as llm
        from program.builtins.models.image import models as image
        from program.builtins.models.audio import models as audio
        from program.builtins.models.video import models as video
        instance = cls()
        for model in llm + image + audio + video:
            instance.register(model)
        return instance

    # Backward-compat aliases
    @classmethod
    def from_builtin(cls) -> ModelRegistry:
        return cls.from_llm_builtins()

    @classmethod
    def from_builtins(cls) -> ModelRegistry:
        return cls.from_image_builtins()

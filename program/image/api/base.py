from __future__ import annotations

from abc import ABC, abstractmethod

from program.image.model.types import Model
from program.image.types import GeneratedImage, ImageContext, ImageOptions


class BaseImageAPI(ABC):
    def __init__(self, options: ImageOptions) -> None:
        self.options = options

    @abstractmethod
    async def generate(self, model: Model, context: ImageContext) -> GeneratedImage: ...

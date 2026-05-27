from __future__ import annotations

from abc import ABC, abstractmethod

from operator_use.inference.model.types import Model
from operator_use.inference.types import (
    AudioOptions,
    STTContext,
    SynthesizedAudio,
    TTSContext,
    TranscribedAudio,
)


class BaseAudioAPI(ABC):
    def __init__(self, options: AudioOptions) -> None:
        self.options = options

    @abstractmethod
    async def synthesize(self, model: Model, context: TTSContext) -> SynthesizedAudio: ...

    @abstractmethod
    async def transcribe(self, model: Model, context: STTContext) -> TranscribedAudio: ...


# Backward-compat alias
BaseAPI = BaseAudioAPI

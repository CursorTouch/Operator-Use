from __future__ import annotations

from dataclasses import fields
from typing import Optional

from program.auth.providers import ProviderAuthManager
from program.inference.api.audio.registry import AudioAPIRegistry
from program.inference.model.registry import ModelRegistry
from program.inference.provider.registry import AudioProviderRegistry, ProviderRegistry
from program.inference.types import AudioOptions, STTContext, SynthesizedAudio, TTSContext, TranscribedAudio


class AudioLLM:
    _models = ModelRegistry.from_audio_builtins()
    _providers = AudioProviderRegistry.from_builtins()
    _apis = AudioAPIRegistry.from_builtins()
    _auth_store = ProviderAuthManager.create(ProviderRegistry.from_builtins())

    def __init__(
        self,
        model_id: str,
        provider: Optional[str] = None,
        options: Optional[AudioOptions] = None,
    ) -> None:
        model = self._models.get(model_id, provider)
        if model is None:
            raise ValueError(f"Audio model '{model_id}' not found.")

        prov = self._providers.get(model.provider)
        if prov is None:
            raise ValueError(f"Audio provider '{model.provider}' not found.")

        api_name = model.api or prov.api
        api_class = self._apis.get(api_name)
        if api_class is None:
            raise ValueError(f"Audio API '{api_name}' not found in registry.")

        self.model = model
        self.provider_id = prov.name

        base_url = model.base_url or prov.base_url
        base_opts = AudioOptions(base_url=base_url)
        self.api = api_class(self._merge_options(base_opts, options))

    def _merge_options(self, base: AudioOptions, override: Optional[AudioOptions]) -> AudioOptions:
        if override is None:
            return base
        merged = AudioOptions(**{f.name: getattr(base, f.name) for f in fields(base)})
        for f in fields(override):
            value = getattr(override, f.name)
            if value is not None:
                setattr(merged, f.name, value)
        return merged

    async def synthesize(self, context: TTSContext) -> SynthesizedAudio:
        api_key = await self._auth_store.get_api_key(self.provider_id)
        if api_key:
            self.api.options.api_key = api_key
        return await self.api.synthesize(self.model, context)

    async def transcribe(self, context: STTContext) -> TranscribedAudio:
        api_key = await self._auth_store.get_api_key(self.provider_id)
        if api_key:
            self.api.options.api_key = api_key
        return await self.api.transcribe(self.model, context)

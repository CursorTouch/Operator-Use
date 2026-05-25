from __future__ import annotations

from dataclasses import fields
from typing import Optional

from program.auth.providers import ProviderAuthManager
from program.inference.api.video.registry import VideoAPIRegistry
from program.inference.model.registry import ModelRegistry
from program.inference.provider.registry import ProviderRegistry, VideoProviderRegistry
from program.inference.types import GeneratedVideo, VideoContext, VideoOptions


class VideoLLM:
    _models    = ModelRegistry.from_video_builtins()
    _providers = VideoProviderRegistry.from_builtins()
    _apis      = VideoAPIRegistry.from_builtins()
    _auth_store = ProviderAuthManager.create(ProviderRegistry.from_builtins())

    def __init__(
        self,
        model_id: str,
        provider: Optional[str] = None,
        options: Optional[VideoOptions] = None,
    ) -> None:
        model = self._models.get(model_id, provider)
        if model is None:
            raise ValueError(f"Video model '{model_id}' not found.")

        prov = self._providers.get(model.provider)
        if prov is None:
            raise ValueError(f"Video provider '{model.provider}' not found.")

        api_name = model.api or prov.api
        api_class = self._apis.get(api_name)
        if api_class is None:
            raise ValueError(f"Video API '{api_name}' not found in registry.")

        self.model = model
        self.provider_id = prov.name

        base_url = model.base_url or prov.base_url
        base_opts = VideoOptions(base_url=base_url)
        self.api = api_class(self._merge_options(base_opts, options))

    def _merge_options(self, base: VideoOptions, override: Optional[VideoOptions]) -> VideoOptions:
        if override is None:
            return base
        merged = VideoOptions(**{f.name: getattr(base, f.name) for f in fields(base)})
        for f in fields(override):
            value = getattr(override, f.name)
            if value is not None:
                setattr(merged, f.name, value)
        return merged

    async def generate(self, context: VideoContext) -> GeneratedVideo:
        api_key = await self._auth_store.get_api_key(self.provider_id)
        if api_key:
            self.api.options.api_key = api_key
        return await self.api.generate(self.model, context)

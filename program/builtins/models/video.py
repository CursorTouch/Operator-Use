from program.inference.model.types import Cost, Model, Modality

_TEXT  = [Modality.Text]
_IMAGE = [Modality.Image]
_VIDEO = [Modality.Video]

models = [
    # Google Veo 3 via fal.ai
    Model(id="fal-ai/veo3",      name="Veo 3",      provider="fal", cost=Cost(), input=_TEXT, output=_VIDEO, api="fal-video"),
    Model(id="fal-ai/veo3-fast", name="Veo 3 Fast", provider="fal", cost=Cost(), input=_TEXT, output=_VIDEO, api="fal-video"),
    # Kling via fal.ai
    Model(id="fal-ai/kling-video/v2.1/standard/text-to-video",  name="Kling v2.1 Standard",     provider="fal", cost=Cost(), input=_TEXT,  output=_VIDEO, api="fal-video"),
    Model(id="fal-ai/kling-video/v2.1/pro/text-to-video",       name="Kling v2.1 Pro",          provider="fal", cost=Cost(), input=_TEXT,  output=_VIDEO, api="fal-video"),
    Model(id="fal-ai/kling-video/v2.1/standard/image-to-video", name="Kling v2.1 Standard I2V", provider="fal", cost=Cost(), input=_IMAGE, output=_VIDEO, api="fal-video"),
    Model(id="fal-ai/kling-video/v2.1/pro/image-to-video",      name="Kling v2.1 Pro I2V",      provider="fal", cost=Cost(), input=_IMAGE, output=_VIDEO, api="fal-video"),
    # Runway Gen4 via fal.ai
    Model(id="fal-ai/runway-gen4/turbo/text-to-video", name="Runway Gen4 Turbo", provider="fal", cost=Cost(), input=_TEXT, output=_VIDEO, api="fal-video"),
    # Hailuo AI via fal.ai
    Model(id="fal-ai/hailuo-ai/video-01",              name="Hailuo Video 01",     provider="fal", cost=Cost(), input=_TEXT,  output=_VIDEO, api="fal-video"),
    Model(id="fal-ai/hailuo-ai/video-01/image-to-video", name="Hailuo Video 01 I2V", provider="fal", cost=Cost(), input=_IMAGE, output=_VIDEO, api="fal-video"),
    # Seedance via fal.ai
    Model(id="fal-ai/seedance-v1/lite/text-to-video", name="Seedance v1 Lite", provider="fal", cost=Cost(), input=_TEXT, output=_VIDEO, api="fal-video"),
    Model(id="fal-ai/seedance-v1/pro/text-to-video",  name="Seedance v1 Pro",  provider="fal", cost=Cost(), input=_TEXT, output=_VIDEO, api="fal-video"),

    # OpenRouter video models
    Model(id="bytedance/seedance-2.0",      name="Seedance 2.0",         provider="openrouter", cost=Cost(), input=[Modality.Text, Modality.Image], output=_VIDEO, api="openrouter-video"),
    Model(id="bytedance/seedance-2.0-fast", name="Seedance 2.0 Fast",    provider="openrouter", cost=Cost(), input=[Modality.Text, Modality.Image], output=_VIDEO, api="openrouter-video"),
    Model(id="bytedance/seedance-1-5-pro",  name="Seedance 1.5 Pro",     provider="openrouter", cost=Cost(), input=[Modality.Text, Modality.Image], output=_VIDEO, api="openrouter-video"),
    Model(id="google/veo-3.1",              name="Veo 3.1",              provider="openrouter", cost=Cost(), input=[Modality.Text, Modality.Image], output=_VIDEO, api="openrouter-video"),
    Model(id="google/veo-3.1-fast",         name="Veo 3.1 Fast",         provider="openrouter", cost=Cost(), input=[Modality.Text, Modality.Image], output=_VIDEO, api="openrouter-video"),
    Model(id="google/veo-3.1-lite",         name="Veo 3.1 Lite",         provider="openrouter", cost=Cost(), input=[Modality.Text, Modality.Image], output=_VIDEO, api="openrouter-video"),
    Model(id="alibaba/wan-2.7",             name="Wan 2.7",              provider="openrouter", cost=Cost(), input=[Modality.Text, Modality.Image], output=_VIDEO, api="openrouter-video"),
    Model(id="alibaba/wan-2.6",             name="Wan 2.6",              provider="openrouter", cost=Cost(), input=[Modality.Text, Modality.Image], output=_VIDEO, api="openrouter-video"),
    Model(id="openai/sora-2-pro",           name="Sora 2 Pro",           provider="openrouter", cost=Cost(), input=[Modality.Text, Modality.Image], output=_VIDEO, api="openrouter-video"),
    Model(id="x-ai/grok-imagine-video",      name="Grok Imagine Video",   provider="openrouter", cost=Cost(), input=[Modality.Text, Modality.Image], output=_VIDEO, api="openrouter-video"),
    Model(id="minimax/hailuo-2.3",          name="Hailuo 2.3",           provider="openrouter", cost=Cost(), input=[Modality.Text, Modality.Image], output=_VIDEO, api="openrouter-video"),
    Model(id="kwaivgi/kling-video-o1",      name="Video O1",             provider="openrouter", cost=Cost(), input=[Modality.Text, Modality.Image], output=_VIDEO, api="openrouter-video"),
    Model(id="kwaivgi/kling-v3.0-pro",      name="Video v3.0 Pro",       provider="openrouter", cost=Cost(), input=[Modality.Text, Modality.Image], output=_VIDEO, api="openrouter-video"),
    Model(id="kwaivgi/kling-v3.0-std",      name="Video v3.0 Standard",  provider="openrouter", cost=Cost(), input=[Modality.Text, Modality.Image], output=_VIDEO, api="openrouter-video"),
]

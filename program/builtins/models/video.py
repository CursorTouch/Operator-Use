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
]

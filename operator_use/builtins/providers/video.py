from operator_use.inference.provider.types import VideoProvider

providers = [
    VideoProvider(name="fal", api="fal-video"),
    VideoProvider(name="openrouter", api="openrouter-video", base_url="https://openrouter.ai/api/v1"),
]

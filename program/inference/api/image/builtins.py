from program.inference.api.image.openrouter import OpenRouterImageAPI
from program.inference.api.image.openai_image import OpenAIImageAPI

# openrouter-images: OpenRouter via /v1/chat/completions with modalities=["image"]
# openai-images:     OpenAI-compatible /v1/images/generations — works for OpenAI,
#                    Together AI, Fireworks AI, DeepInfra, and other compatible providers.
IMAGE_APIS = [
    ("openrouter-image", OpenRouterImageAPI),
    ("openai-image",     OpenAIImageAPI),
]

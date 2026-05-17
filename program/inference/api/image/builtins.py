from program.inference.api.image.openrouter import OpenRouterImageAPI
from program.inference.api.image.openai_image import OpenAIImageAPI
from program.inference.api.image.gemini_image import GeminiImageAPI

IMAGE_APIS = [
    ("openrouter-image", OpenRouterImageAPI),
    ("openai-image",     OpenAIImageAPI),
    ("gemini-image",     GeminiImageAPI),
]

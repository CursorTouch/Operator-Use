from program.inference.provider.types import ImageProvider

providers = [
    ImageProvider(name="openrouter", api="openrouter-image", base_url="https://openrouter.ai/api/v1"),
    ImageProvider(name="openai",     api="openai-image",     base_url="https://api.openai.com/v1"),
    ImageProvider(name="together",   api="openai-image",     base_url="https://api.together.xyz/v1"),
    ImageProvider(name="fireworks",  api="openai-image",     base_url="https://api.fireworks.ai/inference/v1"),
    ImageProvider(name="google",     api="gemini-image",     base_url="https://generativelanguage.googleapis.com"),
]

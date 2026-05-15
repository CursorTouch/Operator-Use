from program.image.provider.types import Provider

PROVIDERS: list[Provider] = [
    Provider(
        name="openrouter",
        api="openrouter-images",
        base_url="https://openrouter.ai/api/v1",
    ),
]

from operator_use.inference.provider.types import AudioProvider

providers = [
    AudioProvider(name="openai",     api="openai-audio"),
    AudioProvider(name="openrouter", api="openai-audio",     base_url="https://openrouter.ai/api/v1"),
    AudioProvider(name="groq",       api="openai-audio",     base_url="https://api.groq.com/openai/v1"),
    AudioProvider(name="google",     api="gemini-audio"),
    AudioProvider(name="sarvam",     api="sarvam-audio",     base_url="https://api.sarvam.ai"),
    AudioProvider(name="elevenlabs", api="elevenlabs-audio", base_url="https://api.elevenlabs.io"),
]

from program.inference.model.types import Cost, Model, Modality

_TEXT_IMAGE = [Modality.Text, Modality.Image]
_TEXT = [Modality.Text]
_IMAGE = [Modality.Image]
_AUDIO = [Modality.Audio]
_TEXT_IMAGE_OUT = [Modality.Text, Modality.Image]

_OPENAI_VOICES  = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse"]
_GROQ_VOICES    = ["autumn", "diana", "hannah", "austin", "daniel", "troy"]

LLM_MODELS: list[Model] = [
    # OpenAI Codex (OAuth) — ChatGPT Plus/Pro Codex subscription
    Model(id="gpt-5.5",              name="GPT-5.5",              provider="openai-codex", cost=Cost(), thinking=True, context_window=400_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gpt-5.4",              name="GPT-5.4",              provider="openai-codex", cost=Cost(), thinking=True, context_window=400_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gpt-5.4-mini",         name="GPT-5.4 Mini",         provider="openai-codex", cost=Cost(), thinking=True, context_window=400_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gpt-5.3-codex",        name="GPT-5.3 Codex",        provider="openai-codex", cost=Cost(), thinking=True, context_window=400_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gpt-5.3-codex-spark",  name="GPT-5.3 Codex Spark",  provider="openai-codex", cost=Cost(), thinking=True, context_window=400_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gpt-5.2",              name="GPT-5.2",              provider="openai-codex", cost=Cost(), thinking=True, context_window=400_000, input=_TEXT_IMAGE, output=_TEXT),
    # Anthropic Claude Code (OAuth)
    Model(id="claude-opus-4-7",          name="Claude Opus 4.7",   provider="anthropic-claude-code", cost=Cost(), thinking=True,  context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="claude-sonnet-4-6",        name="Claude Sonnet 4.6", provider="anthropic-claude-code", cost=Cost(), thinking=True,  context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="claude-haiku-4-5-20251001", name="Claude Haiku 4.5", provider="anthropic-claude-code", cost=Cost(), context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    # GitHub Copilot
    Model(id="gpt-4o",              name="GPT-4o",               provider="github-copilot", cost=Cost(), context_window=128_000,   input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gpt-4o-mini",         name="GPT-4o Mini",          provider="github-copilot", cost=Cost(), context_window=128_000,   input=_TEXT_IMAGE, output=_TEXT),
    Model(id="o3-mini",             name="O3 Mini",              provider="github-copilot", cost=Cost(), thinking=True, context_window=200_000,   input=_TEXT, output=_TEXT),
    Model(id="claude-3.5-sonnet",   name="Claude 3.5 Sonnet",    provider="github-copilot", cost=Cost(), context_window=200_000,   input=_TEXT_IMAGE, output=_TEXT),
    Model(id="claude-3.7-sonnet",   name="Claude 3.7 Sonnet",    provider="github-copilot", cost=Cost(), thinking=True, context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gemini-2.0-flash-001", name="Gemini 2.0 Flash",    provider="github-copilot", cost=Cost(), context_window=1_000_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="grok-3",              name="Grok 3",               provider="github-copilot", cost=Cost(), context_window=131_072,   input=_TEXT, output=_TEXT),
    # OpenAI (API key)
    Model(id="gpt-5.5",      name="GPT-5.5",      provider="openai", cost=Cost(input=5.0,  output=30.0, cache_read=0.50),  thinking=True, context_window=1_050_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gpt-5.4",      name="GPT-5.4",      provider="openai", cost=Cost(input=2.5,  output=15.0, cache_read=0.25),  thinking=True, context_window=1_000_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gpt-5.4-mini", name="GPT-5.4 Mini", provider="openai", cost=Cost(input=0.75, output=4.5,  cache_read=0.075), thinking=True, context_window=1_000_000, input=_TEXT_IMAGE, output=_TEXT),
    # Anthropic (API key)
    Model(id="claude-opus-4-7",           name="Claude Opus 4.7",   provider="anthropic", cost=Cost(input=15.0, output=75.0, cache_read=1.5,   cache_write=3.75),  thinking=True, context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="claude-sonnet-4-6",         name="Claude Sonnet 4.6", provider="anthropic", cost=Cost(input=3.0,  output=15.0, cache_read=0.30,  cache_write=3.75),  thinking=True, context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="claude-haiku-4-5-20251001", name="Claude Haiku 4.5",  provider="anthropic", cost=Cost(input=0.80, output=4.0,  cache_read=0.08,  cache_write=1.0),               context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    # NVIDIA NIM (build.nvidia.com — free dev tier, OpenAI-compatible)
    Model(id="nvidia/llama-3.3-nemotron-super-49b-v1.5",  name="Nemotron Super 49B v1.5", provider="nvidia", cost=Cost(), thinking=True, context_window=128_000,   input=_TEXT, output=_TEXT),
    Model(id="meta/llama-3.3-70b-instruct",               name="Llama 3.3 70B Instruct",  provider="nvidia", cost=Cost(),                context_window=128_000,   input=_TEXT, output=_TEXT),
    Model(id="meta/llama-3.1-8b-instruct",                name="Llama 3.1 8B Instruct",   provider="nvidia", cost=Cost(),                context_window=128_000,   input=_TEXT, output=_TEXT),
    Model(id="qwen/qwen3-235b-a22b",                      name="Qwen3 235B A22B",         provider="nvidia", cost=Cost(), thinking=True, context_window=131_072,   input=_TEXT, output=_TEXT),
    Model(id="deepseek-ai/deepseek-v4-pro",               name="DeepSeek V4 Pro",         provider="nvidia", cost=Cost(), thinking=True, context_window=1_000_000, input=_TEXT, output=_TEXT),
    Model(id="deepseek-ai/deepseek-v4-flash",             name="DeepSeek V4 Flash",       provider="nvidia", cost=Cost(), thinking=True, context_window=1_000_000, input=_TEXT, output=_TEXT),
    Model(id="z-ai/glm5.1",                               name="GLM-5.1",                 provider="nvidia", cost=Cost(), thinking=True, context_window=131_072,   input=_TEXT, output=_TEXT),
    # Groq (OpenAI-compatible — GroqCloud production models)
    Model(id="openai/gpt-oss-120b",                       name="GPT-OSS 120B",          provider="groq", cost=Cost(input=0.15,  output=0.60), thinking=True, context_window=131_072, input=_TEXT, output=_TEXT),
    Model(id="openai/gpt-oss-20b",                        name="GPT-OSS 20B",           provider="groq", cost=Cost(input=0.075, output=0.30), thinking=True, context_window=131_072, input=_TEXT, output=_TEXT),
    Model(id="qwen3-32b",                                 name="Qwen3 32B",             provider="groq", cost=Cost(input=0.29,  output=0.59), thinking=True, context_window=131_072, input=_TEXT, output=_TEXT),
    Model(id="llama-3.3-70b-versatile",                   name="Llama 3.3 70B",         provider="groq", cost=Cost(input=0.59,  output=0.79),                context_window=131_072, input=_TEXT, output=_TEXT),
    Model(id="llama-3.1-8b-instant",                      name="Llama 3.1 8B Instant",  provider="groq", cost=Cost(input=0.05,  output=0.08),                context_window=131_072, input=_TEXT, output=_TEXT),
    Model(id="meta-llama/llama-4-scout-17b-16e-instruct", name="Llama 4 Scout 17Bx16E", provider="groq", cost=Cost(input=0.11,  output=0.34),                context_window=131_072, input=_TEXT_IMAGE, output=_TEXT),

    # Mistral
    Model(id="mistral-large-latest",   name="Mistral Large 3",      provider="mistral", cost=Cost(input=0.50, output=1.50),                 context_window=262_144, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="mistral-medium-latest",  name="Mistral Medium 3.5",   provider="mistral", cost=Cost(input=0.40, output=2.0),                  context_window=131_072, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="mistral-small-latest",   name="Mistral Small 4",      provider="mistral", cost=Cost(input=0.10, output=0.30), thinking=True,  context_window=131_072, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="magistral-medium-latest",name="Magistral Medium 1.2", provider="mistral", cost=Cost(input=2.0,  output=5.0),  thinking=True,  context_window=131_072, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="devstral-latest",        name="Devstral 2",           provider="mistral", cost=Cost(input=0.40, output=2.0),                  context_window=262_144, input=_TEXT, output=_TEXT),
    Model(id="codestral-latest",       name="Codestral",            provider="mistral", cost=Cost(input=0.30, output=0.90),                 context_window=262_144, input=_TEXT, output=_TEXT),
]

AUDIO_MODELS: list[Model] = [
    # OpenAI TTS — text in, audio out
    Model(id="tts-1",           name="TTS-1",           provider="openai", cost=Cost(input=15.0),  input=_TEXT,  output=_AUDIO, api="openai-audio", voices=_OPENAI_VOICES),
    Model(id="tts-1-hd",        name="TTS-1 HD",        provider="openai", cost=Cost(input=30.0),  input=_TEXT,  output=_AUDIO, api="openai-audio", voices=_OPENAI_VOICES),
    Model(id="gpt-4o-mini-tts", name="GPT-4o Mini TTS", provider="openai", cost=Cost(input=0.60),  input=_TEXT,  output=_AUDIO, api="openai-audio", voices=_OPENAI_VOICES),
    # OpenAI STT — audio in, text out
    Model(id="whisper-1",              name="Whisper 1",              provider="openai", cost=Cost(input=0.006), input=_AUDIO, output=_TEXT, api="openai-audio"),
    Model(id="gpt-4o-transcribe",      name="GPT-4o Transcribe",      provider="openai", cost=Cost(input=2.5),   input=_AUDIO, output=_TEXT, api="openai-audio"),
    Model(id="gpt-4o-mini-transcribe", name="GPT-4o Mini Transcribe", provider="openai", cost=Cost(input=0.003), input=_AUDIO, output=_TEXT, api="openai-audio"),
    # Google Gemini TTS — text in, PCM audio out (no format selection, no STT)
    Model(id="gemini-2.5-flash-preview-tts", name="Gemini 2.5 Flash TTS", provider="google", cost=Cost(input=0.50,  output=10.0), input=_TEXT, output=_AUDIO, api="gemini-audio"),
    Model(id="gemini-2.5-pro-preview-tts",   name="Gemini 2.5 Pro TTS",   provider="google", cost=Cost(input=2.00,  output=16.0), input=_TEXT, output=_AUDIO, api="gemini-audio"),
    Model(id="gemini-3.1-flash-tts-preview", name="Gemini 3.1 Flash TTS", provider="google", cost=Cost(input=0.10,  output=1.00), input=_TEXT, output=_AUDIO, api="gemini-audio"),
    # Sarvam AI — Indian language TTS + STT
    Model(id="bulbul:v3",    name="Bulbul v3",    provider="sarvam", cost=Cost(), input=_TEXT,  output=_AUDIO, api="sarvam-audio"),
    Model(id="saarika:v2.5", name="Saarika v2.5", provider="sarvam", cost=Cost(), input=_AUDIO, output=_TEXT,  api="sarvam-audio"),
    Model(id="saaras:v3",    name="Saaras v3",    provider="sarvam", cost=Cost(), input=_AUDIO, output=_TEXT,  api="sarvam-audio"),
    # ElevenLabs TTS + STT
    Model(id="eleven_multilingual_v2", name="Eleven Multilingual v2", provider="elevenlabs", cost=Cost(input=0.30), input=_TEXT,  output=_AUDIO, api="elevenlabs-audio"),
    Model(id="eleven_flash_v2_5",      name="Eleven Flash v2.5",      provider="elevenlabs", cost=Cost(input=0.08), input=_TEXT,  output=_AUDIO, api="elevenlabs-audio"),
    Model(id="eleven_turbo_v2_5",      name="Eleven Turbo v2.5",      provider="elevenlabs", cost=Cost(input=0.15), input=_TEXT,  output=_AUDIO, api="elevenlabs-audio"),
    Model(id="scribe_v1",              name="Scribe v1",               provider="elevenlabs", cost=Cost(input=0.40), input=_AUDIO, output=_TEXT,  api="elevenlabs-audio"),
    Model(id="scribe_v2",              name="Scribe v2",               provider="elevenlabs", cost=Cost(input=0.40), input=_AUDIO, output=_TEXT,  api="elevenlabs-audio"),
    # Groq TTS — text in, audio out
    Model(id="canopylabs/orpheus-v1-english", name="Orpheus v1 English", provider="groq", cost=Cost(), input=_TEXT, output=_AUDIO, api="openai-audio", voices=_GROQ_VOICES),
    # Groq STT — audio in, text out (Whisper on Groq)
    Model(id="whisper-large-v3",       name="Whisper Large v3",       provider="groq", cost=Cost(input=0.111), input=_AUDIO, output=_TEXT, api="openai-audio"),
    Model(id="whisper-large-v3-turbo", name="Whisper Large v3 Turbo", provider="groq", cost=Cost(input=0.04),  input=_AUDIO, output=_TEXT, api="openai-audio"),
]

_VIDEO = [Modality.Video]

VIDEO_MODELS: list[Model] = [
    # Google Veo 3 via fal.ai
    Model(id="fal-ai/veo3",                                     name="Veo 3",                  provider="fal", cost=Cost(), input=_TEXT,       output=_VIDEO, api="fal-video"),
    Model(id="fal-ai/veo3-fast",                                name="Veo 3 Fast",             provider="fal", cost=Cost(), input=_TEXT,       output=_VIDEO, api="fal-video"),
    # Kling via fal.ai
    Model(id="fal-ai/kling-video/v2.1/standard/text-to-video", name="Kling v2.1 Standard",    provider="fal", cost=Cost(), input=_TEXT,       output=_VIDEO, api="fal-video"),
    Model(id="fal-ai/kling-video/v2.1/pro/text-to-video",      name="Kling v2.1 Pro",         provider="fal", cost=Cost(), input=_TEXT,       output=_VIDEO, api="fal-video"),
    Model(id="fal-ai/kling-video/v2.1/standard/image-to-video",name="Kling v2.1 Standard I2V", provider="fal", cost=Cost(), input=_IMAGE,      output=_VIDEO, api="fal-video"),
    Model(id="fal-ai/kling-video/v2.1/pro/image-to-video",     name="Kling v2.1 Pro I2V",     provider="fal", cost=Cost(), input=_IMAGE,      output=_VIDEO, api="fal-video"),
    # Runway Gen4 via fal.ai
    Model(id="fal-ai/runway-gen4/turbo/text-to-video",         name="Runway Gen4 Turbo",      provider="fal", cost=Cost(), input=_TEXT,       output=_VIDEO, api="fal-video"),
    # Hailuo AI via fal.ai
    Model(id="fal-ai/hailuo-ai/video-01",                      name="Hailuo Video 01",        provider="fal", cost=Cost(), input=_TEXT,       output=_VIDEO, api="fal-video"),
    Model(id="fal-ai/hailuo-ai/video-01/image-to-video",       name="Hailuo Video 01 I2V",    provider="fal", cost=Cost(), input=_IMAGE,      output=_VIDEO, api="fal-video"),
    # Seedance via fal.ai
    Model(id="fal-ai/seedance-v1/lite/text-to-video",          name="Seedance v1 Lite",       provider="fal", cost=Cost(), input=_TEXT,       output=_VIDEO, api="fal-video"),
    Model(id="fal-ai/seedance-v1/pro/text-to-video",           name="Seedance v1 Pro",        provider="fal", cost=Cost(), input=_TEXT,       output=_VIDEO, api="fal-video"),
]

IMAGE_MODELS: list[Model] = [
    # OpenAI DALL-E (native /v1/images/generations)
    Model(id="dall-e-3", name="DALL-E 3", provider="openai", cost=Cost(input=40.0), input=_TEXT, output=_IMAGE, api="openai-image"),
    Model(id="dall-e-2", name="DALL-E 2", provider="openai", cost=Cost(input=20.0), input=_TEXT, output=_IMAGE, api="openai-image"),
    # Together AI — OpenAI-compatible image generation
    Model(id="black-forest-labs/FLUX.1-schnell-Free", name="FLUX.1 Schnell Free", provider="together", cost=Cost(),             input=_TEXT, output=_IMAGE, api="openai-image"),
    Model(id="black-forest-labs/FLUX.1-schnell",      name="FLUX.1 Schnell",      provider="together", cost=Cost(input=0.053),  input=_TEXT, output=_IMAGE, api="openai-image"),
    Model(id="black-forest-labs/FLUX.1-dev",          name="FLUX.1 Dev",          provider="together", cost=Cost(input=0.35),   input=_TEXT, output=_IMAGE, api="openai-image"),
    Model(id="black-forest-labs/FLUX.1.1-pro",        name="FLUX.1.1 Pro",        provider="together", cost=Cost(input=0.40),   input=_TEXT, output=_IMAGE, api="openai-image"),
    # Fireworks AI — OpenAI-compatible image generation
    Model(id="accounts/fireworks/models/flux-1-schnell-fp8", name="FLUX.1 Schnell FP8", provider="fireworks", cost=Cost(input=0.053), input=_TEXT, output=_IMAGE, api="openai-image"),
    Model(id="accounts/fireworks/models/flux-1-dev-fp8",     name="FLUX.1 Dev FP8",     provider="fireworks", cost=Cost(input=0.35),  input=_TEXT, output=_IMAGE, api="openai-image"),
    # Black Forest Labs FLUX
    Model(id="black-forest-labs/flux-2-flex",  name="FLUX.2 Flex",       provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="black-forest-labs/flux-2-klein", name="FLUX.2 Klein 4B",   provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="black-forest-labs/flux-2-max",   name="FLUX.2 Max",        provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="black-forest-labs/flux-2-pro",   name="FLUX.2 Pro",        provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    # Google Imagen (native gemini-image API)
    Model(id="imagen-3.0-generate-002",      name="Imagen 3",      provider="google", cost=Cost(input=0.04), input=_TEXT, output=_IMAGE, api="gemini-image"),
    Model(id="imagen-3.0-fast-generate-001", name="Imagen 3 Fast", provider="google", cost=Cost(input=0.02), input=_TEXT, output=_IMAGE, api="gemini-image"),
    # Google Gemini Image via OpenRouter
    Model(id="google/gemini-2.5-flash-image-generation",         name="Gemini 2.5 Flash Image",         provider="openrouter", cost=Cost(input=0.30,  output=2.50),  input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
    Model(id="google/gemini-3-pro-image-generation-preview",     name="Gemini 3 Pro Image Preview",     provider="openrouter", cost=Cost(input=2.00,  output=12.00), input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
    Model(id="google/gemini-3.1-flash-image-generation-preview", name="Gemini 3.1 Flash Image Preview", provider="openrouter", cost=Cost(input=0.50,  output=3.00),  input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
    # OpenAI GPT Image
    Model(id="openai/gpt-5-image",      name="GPT-5 Image",      provider="openrouter", cost=Cost(input=10.00, output=10.00), input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
    Model(id="openai/gpt-5-image-mini", name="GPT-5 Image Mini", provider="openrouter", cost=Cost(input=2.50,  output=2.00),  input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
    Model(id="openai/gpt-5.4-image-2",  name="GPT-5.4 Image 2", provider="openrouter", cost=Cost(input=8.00,  output=15.00), input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
    # ByteDance
    Model(id="bytedance/seedream-4.5", name="Seedream 4.5", provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    # Sourceful Riverflow V2
    Model(id="sourceful/riverflow-v2",       name="Riverflow V2",       provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="sourceful/riverflow-v2-turbo", name="Riverflow V2 Turbo", provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="sourceful/riverflow-v2-max",   name="Riverflow V2 Max",   provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="sourceful/riverflow-v2-pro",   name="Riverflow V2 Pro",   provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    # OpenRouter Auto
    Model(id="openrouter/auto", name="OpenRouter Auto", provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
]

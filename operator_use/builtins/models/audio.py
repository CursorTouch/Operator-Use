from operator_use.inference.model.types import Cost, Model, Modality

_TEXT  = [Modality.Text]
_AUDIO = [Modality.Audio]

_OPENAI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse"]
_GROQ_VOICES   = ["autumn", "diana", "hannah", "austin", "daniel", "troy"]

models = [
    # OpenAI TTS
    Model(id="tts-1",           name="TTS-1",           provider="openai", cost=Cost(input=15.0), input=_TEXT,  output=_AUDIO, api="openai-audio", voices=_OPENAI_VOICES),
    Model(id="tts-1-hd",        name="TTS-1 HD",        provider="openai", cost=Cost(input=30.0), input=_TEXT,  output=_AUDIO, api="openai-audio", voices=_OPENAI_VOICES),
    Model(id="gpt-4o-mini-tts", name="GPT-4o Mini TTS", provider="openai", cost=Cost(input=0.60), input=_TEXT,  output=_AUDIO, api="openai-audio", voices=_OPENAI_VOICES),
    # OpenAI STT
    Model(id="whisper-1",              name="Whisper 1",              provider="openai", cost=Cost(input=0.006), input=_AUDIO, output=_TEXT, api="openai-audio"),
    Model(id="gpt-4o-transcribe",      name="GPT-4o Transcribe",      provider="openai", cost=Cost(input=2.5),   input=_AUDIO, output=_TEXT, api="openai-audio"),
    Model(id="gpt-4o-mini-transcribe", name="GPT-4o Mini Transcribe", provider="openai", cost=Cost(input=0.003), input=_AUDIO, output=_TEXT, api="openai-audio"),
    # Google Gemini TTS
    Model(id="gemini-2.5-flash-preview-tts", name="Gemini 2.5 Flash TTS", provider="google", cost=Cost(input=0.50,  output=10.0), input=_TEXT, output=_AUDIO, api="gemini-audio"),
    Model(id="gemini-2.5-pro-preview-tts",   name="Gemini 2.5 Pro TTS",   provider="google", cost=Cost(input=2.00,  output=16.0), input=_TEXT, output=_AUDIO, api="gemini-audio"),
    Model(id="gemini-3.1-flash-tts-preview", name="Gemini 3.1 Flash TTS", provider="google", cost=Cost(input=0.10,  output=1.00), input=_TEXT, output=_AUDIO, api="gemini-audio"),
    # OpenRouter speech models
    Model(id="openai/gpt-audio",          name="GPT Audio",         provider="openrouter", cost=Cost(input=2.50, output=10.00), input=_TEXT, output=_AUDIO, api="openai-audio"),
    Model(id="openai/gpt-audio-mini",     name="GPT Audio Mini",    provider="openrouter", cost=Cost(input=0.60, output=2.40),  input=_TEXT, output=_AUDIO, api="openai-audio"),
    Model(id="openai/gpt-4o-audio-preview", name="GPT-4o Audio",    provider="openrouter", cost=Cost(input=2.50, output=10.00), input=_TEXT, output=_AUDIO, api="openai-audio"),
    Model(id="google/lyria-3-pro-preview", name="Lyria 3 Pro Preview", provider="openrouter", cost=Cost(), input=_TEXT, output=_AUDIO, api="openai-audio"),
    Model(id="google/lyria-3-clip-preview", name="Lyria 3 Clip Preview", provider="openrouter", cost=Cost(), input=_TEXT, output=_AUDIO, api="openai-audio"),
    # Sarvam AI
    Model(id="bulbul:v3",    name="Bulbul v3",    provider="sarvam", cost=Cost(), input=_TEXT,  output=_AUDIO, api="sarvam-audio"),
    Model(id="saarika:v2.5", name="Saarika v2.5", provider="sarvam", cost=Cost(), input=_AUDIO, output=_TEXT,  api="sarvam-audio"),
    Model(id="saaras:v3",    name="Saaras v3",    provider="sarvam", cost=Cost(), input=_AUDIO, output=_TEXT,  api="sarvam-audio"),
    # ElevenLabs
    Model(id="eleven_multilingual_v2", name="Eleven Multilingual v2", provider="elevenlabs", cost=Cost(input=0.30), input=_TEXT,  output=_AUDIO, api="elevenlabs-audio"),
    Model(id="eleven_flash_v2_5",      name="Eleven Flash v2.5",      provider="elevenlabs", cost=Cost(input=0.08), input=_TEXT,  output=_AUDIO, api="elevenlabs-audio"),
    Model(id="eleven_turbo_v2_5",      name="Eleven Turbo v2.5",      provider="elevenlabs", cost=Cost(input=0.15), input=_TEXT,  output=_AUDIO, api="elevenlabs-audio"),
    Model(id="scribe_v1",              name="Scribe v1",               provider="elevenlabs", cost=Cost(input=0.40), input=_AUDIO, output=_TEXT,  api="elevenlabs-audio"),
    Model(id="scribe_v2",              name="Scribe v2",               provider="elevenlabs", cost=Cost(input=0.40), input=_AUDIO, output=_TEXT,  api="elevenlabs-audio"),
    # Groq TTS
    Model(id="canopylabs/orpheus-v1-english", name="Orpheus v1 English", provider="groq", cost=Cost(), input=_TEXT, output=_AUDIO, api="openai-audio", voices=_GROQ_VOICES),
    # Groq STT
    Model(id="whisper-large-v3",       name="Whisper Large v3",       provider="groq", cost=Cost(input=0.111), input=_AUDIO, output=_TEXT, api="openai-audio"),
    Model(id="whisper-large-v3-turbo", name="Whisper Large v3 Turbo", provider="groq", cost=Cost(input=0.04),  input=_AUDIO, output=_TEXT, api="openai-audio"),
]

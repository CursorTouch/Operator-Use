# Inference

The inference layer provides a unified interface to multiple providers across three modalities: **text** (LLM streaming), **image** (generation), and **audio** (TTS and STT). Each modality has its own registry stack — models, providers, API implementations — and a single entry-point service class that resolves all three at construction time.

---

## Text (LLM)

`LLM` resolves a model ID to a concrete API instance, handling auth for both API-key and OAuth providers:

```python
llm = LLM(model_id="claude-sonnet-4-5", provider="anthropic")

# Streaming
async for event in llm.stream(context):
    match event:
        case TextDeltaEvent(text=t): ...
        case ToolCallEndEvent(tool_call=tc): ...
        case EndEvent(): ...

# Non-streaming (waits for completion)
events = await llm.invoke(context, thinking_level=ThinkingLevel.Low)
```

Resolution order at construction:

1. Look up the model in `ModelRegistry` (by `model_id`, optionally filtered by `provider`).
2. Look up the provider in `ProviderRegistry`.
3. Resolve the API class: from `model.api`, then `provider.api`, then `LLMAPIRegistry`.
4. If the provider is an `OAuthProvider`: load credentials from `AuthManager`. If missing, raise immediately.
5. Merge provider base options with any caller-supplied `LLMOptions`.
6. Set `max_tokens` from the model definition if the API options leave it unset.

`LLM._apis`, `LLM._models`, `LLM._providers`, and `LLM._auth_store` are class-level registries, shared across all instances.

### API implementations

Each API is a class in `program/inference/api/text/` that wraps one HTTP endpoint style:

| Module | Provider(s) |
|---|---|
| `anthropic_messages.py` | Anthropic Messages API |
| `anthropic_claude_code.py` | Anthropic via Claude Code OAuth |
| `openai_completions.py` | OpenAI Chat Completions (and compatible: Groq, NVIDIA, Ollama, etc.) |
| `openai_responses.py` | OpenAI Responses API |
| `openai_codex_responses.py` | OpenAI Codex |
| `gemini_generate.py` | Google Gemini |
| `google_antigravity.py` | Google internal (antigravity) |
| `github_copilot_chat.py` | GitHub Copilot |
| `mistral_chat.py` | Mistral |
| `ollama_chat.py` | Ollama (local) |

All implementations conform to `BaseLLMAPI`:

```python
class BaseLLMAPI:
    def __init__(self, options: LLMOptions) -> None: ...
    async def stream(self, context: LLMContext) -> AsyncIterator[LLMEvent]: ...
    async def invoke(self, context: LLMContext, model: Model) -> list[LLMEvent]: ...
```

`LLMContext` carries:

```python
@dataclass
class LLMContext:
    messages: list[BaseMessage]
    system_prompt: str | None = None
    tools: list[Tool] | None = None
```

### Event stream

All text APIs emit a standardized event stream:

| Event | Meaning |
|---|---|
| `TextDeltaEvent(text)` | Streaming text chunk |
| `TextEndEvent(text)` | Completed text block |
| `ThinkingDeltaEvent(thinking)` | Streaming thinking chunk |
| `ThinkingEndEvent(thinking)` | Completed thinking block |
| `ToolCallEndEvent(tool_call)` | Completed tool call (name + input JSON) |
| `ErrorEvent(reason, error)` | Provider error |
| `EndEvent(reason, input_tokens, output_tokens, ...)` | Stream complete with usage |

`StopReason` values: `Stop`, `ToolCalls`, `Error`, `Abort`.

Providers that do not stream natively still emit the same events in a single batch.

### ThinkingLevel

```python
class ThinkingLevel(str, Enum):
    Off = "off"
    Minimal = "minimal"
    Low = "low"
    Medium = "medium"
    High = "high"
    XHigh = "xhigh"
```

Passed through to API implementations that support extended thinking (Anthropic). APIs that do not support it ignore it.

---

## Image generation

`ImageLLM` handles image generation through `program/inference/api/image/`. The interface mirrors the text one but uses a single non-streaming `generate()` call:

```python
from program.inference.types import ImageContext
from program.message.types import TextContent

svc = ImageLLM("dall-e-3")
result = await svc.generate(ImageContext(
    contents=[TextContent(content="A red fox in a snowy forest")],
    size="1024x1024",
    quality="hd",
))
# result.output → list[TextContent | ImageContent]
```

`GeneratedImage` carries the output as a list of `TextContent | ImageContent`, the stop reason, and token usage. `ImageContext` supports `size`, `quality`, and `n` (number of images).

### Image API standards

Two distinct API styles exist:

**OpenAI-compatible** — `POST /v1/images/generations`. Returns `data[].b64_json` or `data[].url`. A single `OpenAIImageAPI` class serves all compatible providers:

| Provider | `provider` name | `base_url` |
|---|---|---|
| OpenAI (DALL-E) | `openai` | `https://api.openai.com/v1` |
| Together AI | `together` | `https://api.together.xyz/v1` |
| Fireworks AI | `fireworks` | `https://api.fireworks.ai/inference/v1` |

**OpenRouter** — `POST /v1/chat/completions` with `modalities: ["image", "text"]`. Images returned in `choices[0].message.images[]`. Handled by `OpenRouterImageAPI` (`openrouter-image`).

### Built-in image models

| Model ID | Provider | Notes |
|---|---|---|
| `dall-e-3` | openai | Supports `size`, `quality` (standard/hd) |
| `dall-e-2` | openai | |
| `black-forest-labs/FLUX.1-schnell-Free` | together | Free tier |
| `black-forest-labs/FLUX.1-schnell` | together | |
| `black-forest-labs/FLUX.1-dev` | together | |
| `black-forest-labs/FLUX.1.1-pro` | together | |
| `accounts/fireworks/models/flux-1-schnell-fp8` | fireworks | |
| `accounts/fireworks/models/flux-1-dev-fp8` | fireworks | |
| FLUX.2 / Gemini / GPT-Image variants | openrouter | Via chat completions |

---

## Audio (TTS / STT)

`AudioText` handles text-to-speech and speech-to-text through `program/inference/api/audio/`. It uses the same registry/auth pattern as `LLM` and `ImageLLM`.

```python
from program.inference.api.audio.service import AudioText
from program.inference.types import TTSContext, STTContext, AudioFormat

# Text-to-speech
svc = AudioText("tts-1")                         # reads OPENAI_API_KEY from env/auth
result = await svc.synthesize(TTSContext(
    input="Hello, world.",
    voice="alloy",
    response_format=AudioFormat.MP3,
    speed=1.0,
))
# result.audio  → raw bytes
# result.format → AudioFormat.MP3

# Speech-to-text
svc = AudioText("whisper-large-v3")              # reads GROQ_API_KEY from env/auth
result = await svc.transcribe(STTContext(
    audio=audio_bytes,
    format=AudioFormat.MP3,
    language="en",
))
# result.text     → transcript string
# result.words    → list[WordTimestamp]
# result.segments → list[SegmentTimestamp]
```

### Provider API standards

There are two distinct audio API standards in use:

**OpenAI-compatible** — `/v1/audio/speech` (TTS) and `/v1/audio/transcriptions` (STT). The de facto standard, the same way `/v1/chat/completions` is the standard for LLM text. Groq implements it identically — only `base_url` differs. A single `OpenAIAudioAPI` class serves both.

**Gemini** — TTS via `generate_content` with `response_modalities=["AUDIO"]` and a `SpeechConfig`. Different SDK, different response shape, always outputs raw PCM (s16le, 24 kHz, mono). No STT endpoint available in the generative API.

| Provider | `provider` name | API class | TTS | STT |
|---|---|---|---|---|
| OpenAI | `openai` | `OpenAIAudioAPI` | ✓ | ✓ |
| Groq | `groq` | `OpenAIAudioAPI` | ✓ | ✓ |
| Google Gemini | `google` | `GeminiAudioAPI` | ✓ | ✗ |
| Sarvam AI | `sarvam` | `SarvamAudioAPI` | ✓ | ✓ |
| ElevenLabs | `elevenlabs` | `ElevenLabsAudioAPI` | ✓ | ✓ |

### Base class

```python
class BaseAudioAPI(ABC):
    def __init__(self, options: AudioOptions) -> None: ...

    @abstractmethod
    async def synthesize(self, model: Model, context: TTSContext) -> SynthesizedAudio: ...

    @abstractmethod
    async def transcribe(self, model: Model, context: STTContext) -> TranscribedAudio: ...
```

### Context types

```python
@dataclass
class TTSContext:
    input: str
    voice: str
    speed: float = 1.0
    response_format: AudioFormat = AudioFormat.MP3
    instructions: str | None = None      # style/tone hint (OpenAI newer models)

@dataclass
class STTContext:
    audio: bytes
    format: AudioFormat = AudioFormat.MP3
    language: str | None = None
    temperature: float = 0.0
    timestamp_granularities: list[TimestampGranularity] = []
    prompt: str | None = None            # context hint for accuracy
```

### Result types

```python
@dataclass
class SynthesizedAudio:
    model_id: str
    provider: str
    audio: bytes
    format: AudioFormat
    stop_reason: AudioStopReason
    usage: Any = None
    error: str = ""

@dataclass
class TranscribedAudio:
    model_id: str
    provider: str
    text: str
    language: str | None = None
    duration: float | None = None
    words: list[WordTimestamp] = []
    segments: list[SegmentTimestamp] = []
    stop_reason: AudioStopReason = AudioStopReason.Stop
    usage: Any = None
    error: str = ""
```

### AudioFormat

```python
class AudioFormat(str, Enum):
    MP3 = "mp3"
    WAV = "wav"
    OPUS = "opus"
    AAC = "aac"
    FLAC = "flac"
    PCM = "pcm"
```

### Built-in audio models

| Model ID | Provider | Direction | Notes |
|---|---|---|---|
| `tts-1` | openai | text → audio | Standard TTS |
| `tts-1-hd` | openai | text → audio | High-quality TTS |
| `gpt-4o-mini-tts` | openai | text → audio | Instructable TTS |
| `whisper-1` | openai | audio → text | Whisper v2 |
| `gpt-4o-transcribe` | openai | audio → text | GPT-4o transcription |
| `gpt-4o-mini-transcribe` | openai | audio → text | GPT-4o Mini transcription |
| `gemini-2.5-flash-preview-tts` | google | text → audio | PCM output, 30 voices |
| `gemini-2.5-pro-preview-tts` | google | text → audio | PCM output, 30 voices |
| `gemini-3.1-flash-tts-preview` | google | text → audio | PCM output, 30 voices |
| `bulbul:v3` | sarvam | text → audio | 30+ voices, 11 Indian languages |
| `saarika:v2.5` | sarvam | audio → text | 22 Indian languages |
| `saaras:v3` | sarvam | audio → text | 22 languages, multi-mode (transcribe/translate/transliterate) |
| `eleven_multilingual_v2` | elevenlabs | text → audio | 32 languages, 3000+ voices |
| `eleven_flash_v2_5` | elevenlabs | text → audio | Low-latency, 32 languages |
| `eleven_turbo_v2_5` | elevenlabs | text → audio | Balanced quality/speed |
| `scribe_v1` | elevenlabs | audio → text | Diarization, audio event tagging |
| `scribe_v2` | elevenlabs | audio → text | Latest Scribe model |
| `canopylabs/orpheus-v1-english` | groq | text → audio | Expressive English TTS |
| `whisper-large-v3` | groq | audio → text | Fast Whisper on Groq |
| `whisper-large-v3-turbo` | groq | audio → text | Faster Whisper on Groq |

> **Gemini:** Always returns raw PCM (s16le, 24 kHz, mono) regardless of `TTSContext.response_format`. `transcribe()` raises `NotImplementedError`.
>
> **Sarvam:** Auth uses `api-subscription-key` header. TTS response is JSON with base64-encoded WAV in `audios[]`. For `saaras:v3` STT, set `STTContext.prompt` to the desired mode: `transcribe` (default), `translate`, `verbatim`, `translit`, or `codemix`. Set `TTSContext.language` / `STTContext.language` to a BCP-47 code (e.g. `hi-IN`, `en-IN`).
>
> **ElevenLabs:** Auth uses `xi-api-key` header. `TTSContext.voice` maps to `voice_id` in the URL path — use an ElevenLabs voice ID string (e.g. `"21m00Tcm4TlvDq8ikWAM"`). `TTSContext.speed` maps to `voice_settings.speed`. TTS returns raw audio bytes. STT words list only includes `type="word"` entries (spacing and audio events are filtered out). Env var: `ELEVENLABS_API_KEY`.

---

## Model registry

`ModelRegistry` maps `(model_id, provider)` pairs to `Model` objects. Each `Model` carries:

```python
@dataclass
class Model:
    id: str
    provider: str
    api: str | type    # API class or registry name
    max_tokens: int
    context_window: int
    input: list[Modality]
    output: list[Modality]
    base_url: str | None = None
```

`Modality` covers `Text`, `Image`, and `Audio`. Factory methods load the appropriate built-in list:

| Method | Loads |
|---|---|
| `ModelRegistry.from_llm_builtins()` | LLM text models |
| `ModelRegistry.from_image_builtins()` | Image models |
| `ModelRegistry.from_audio_builtins()` | Audio models |
| `ModelRegistry.from_all_builtins()` | All three combined |

---

## Provider registry

### Text providers

`ProviderRegistry` holds `APIProvider` and `OAuthProvider` definitions used by `LLM`.

**APIProvider** — key-based auth. The provider's `LLMOptions` carries the base URL; the API key is resolved at call time via `AuthManager`.

**OAuthProvider** — OAuth token auth. At construction, `LLM` loads credentials from `AuthManager` and derives a temporary API key from the stored `OAuthCredential`.

### Image providers

`ImageProviderRegistry` holds `ImageProvider` definitions — a name, an API registry key, and a `base_url`.

### Audio providers

`AudioProviderRegistry` holds `AudioProvider` definitions:

```python
@dataclass
class AudioProvider:
    name: str
    api: str            # key into AudioAPIRegistry
    base_url: str | None = None
```

Built-in audio providers: `openai` and `groq` — both resolved through `OpenAIAudioAPI`.

---

## Auth

`AuthManager` persists credentials to `auth.json` in the config directory. It supports:

- `OAuthCredential` — access token, refresh token, expiry
- `APICredential` — plain API key string

All three service classes (`LLM`, `ImageLLM`, `AudioText`) share the same `AuthManager` backed by the same credential store. `AudioText._auth_store` is initialized with the LLM `ProviderRegistry` so that stored `openai` and `groq` credentials (saved via the LLM auth flow) are automatically available for audio calls too.

API key resolution order (for `AuthManager.get_api_key(provider)`):

1. Runtime override — `auth_store.set_runtime_api_key("openai", key)`
2. Stored `APICredential` in `auth.json`
3. Environment variable — `f"{provider.upper()}_API_KEY"` (e.g. `OPENAI_API_KEY`, `GROQ_API_KEY`)

OAuth flows are implemented per-provider in `program/inference/provider/oauth/`:

| Module | Provider |
|---|---|
| `anthropic_claude_code.py` | Anthropic Claude Code |
| `github_copilot.py` | GitHub Copilot |
| `google_antigravity.py` | Google |
| `openai_codex.py` | OpenAI Codex |
| `pkce.py` | Generic PKCE helper |

---

## Related documents

- [engine.md](./engine.md) — How Engine calls `llm.stream()` and processes events
- [agent.md](./agent.md) — How the Agent's model and provider are resolved
- [auth.md](./auth.md) — Credential storage and OAuth flows in detail
- [extensions.md](./extensions.md) — How extensions can affect model selection

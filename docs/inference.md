# Inference

The inference layer provides a unified streaming interface to multiple LLM providers. It is organized as a set of registries: models, providers, and API implementations. `LLM` is the single entry point that resolves these at construction time.

## LLM

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

## Model registry

`ModelRegistry` maps `(model_id, provider)` pairs to `Model` objects. Each `Model` carries:

```python
@dataclass
class Model:
    id: str
    provider: str
    api: str | type    # API class or name to look up in LLMAPIRegistry
    max_tokens: int
    context_window: int
    base_url: str | None = None
    # ... additional metadata
```

`ModelRegistry.from_llm_builtins()` loads all built-in model definitions. The registry supports `get(model_id, provider=None)` which returns the model if found, or `None`.

## Provider registry

`ProviderRegistry` holds `APIProvider` and `OAuthProvider` definitions.

**APIProvider** — key-based auth. The provider's `LLMOptions` carries the API key, read from an environment variable or `auth.json`.

**OAuthProvider** — OAuth token auth. At construction, `LLM` loads credentials from `AuthManager` and derives a temporary API key from the stored `OAuthCredential`. The derived key expires; re-login is needed when it does.

## API implementations

Each API is a class in `program/inference/api/llm/` that wraps one HTTP endpoint style:

| Module | Provider(s) |
|---|---|
| `anthropic_messages.py` | Anthropic Messages API |
| `anthropic_claude_code.py` | Anthropic via Claude Code OAuth |
| `openai_completions.py` | OpenAI Chat Completions (and compatible) |
| `openai_responses.py` | OpenAI Responses API |
| `openai_codex_responses.py` | OpenAI Codex |
| `gemini_generate.py` | Google Gemini |
| `google_antigravity.py` | Google internal (antigravity) |
| `github_copilot_chat.py` | GitHub Copilot |
| `mistral_chat.py` | Mistral |
| `ollama_chat.py` | Ollama (local) |

All implementations conform to the same interface:

```python
class BaseLLMAPI:
    def __init__(self, options: LLMOptions) -> None: ...

    async def stream(self, context: LLMContext) -> AsyncIterator[LLMEvent]: ...
```

`LLMContext` carries:

```python
@dataclass
class LLMContext:
    messages: list[BaseMessage]
    system_prompt: str | None = None
    tools: list[Tool] | None = None
```

## Event stream

All APIs emit a standardized event stream:

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

## Auth

`AuthManager` persists credentials to `auth.json` in the config directory. It supports:

- `OAuthCredential` — access token, refresh token, expiry
- `APIKeyCredential` — plain API key string

`LLM._auth_store` is a class-level `AuthManager` shared across all `LLM` instances. This means a credential stored at login time is visible to any subsequently constructed `LLM`.

OAuth flows are implemented per-provider in `program/inference/provider/oauth/`:

| Module | Provider |
|---|---|
| `anthropic_claude_code.py` | Anthropic Claude Code |
| `github_copilot.py` | GitHub Copilot |
| `google_antigravity.py` | Google |
| `openai_codex.py` | OpenAI Codex |
| `pkce.py` | Generic PKCE helper |

Each OAuth module implements the login flow (browser redirect + token exchange) and token refresh.

## Image generation

A parallel registry (`ImageAPIRegistry`, `ImageModelRegistry`) handles image generation providers in `program/inference/api/image/`. The interface mirrors the LLM one but the context and event types differ.

## LLMOptions

```python
@dataclass
class LLMOptions:
    api_key: str | None = None
    base_url: str | None = None
    max_tokens: int | None = None
    # Provider-specific extras...
```

Options from the provider and the caller are merged at `LLM.__init__()` — caller values override provider defaults. Provider-set fields that the caller leaves as `None` are preserved.

## ThinkingLevel

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

## Related documents

- [engine.md](./engine.md) — How Engine calls `llm.stream()` and processes events
- [agent.md](./agent.md) — How the Agent's model and provider are resolved
- [extensions.md](./extensions.md) — How extensions can affect model selection

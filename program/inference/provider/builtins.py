from program.inference.api.text.openai_responses import OpenAIResponsesAPI
from program.inference.api.text.openai_completions import OpenAICompletionsAPI
from program.inference.api.text.anthropic_messages import AnthropicMessagesAPI
from program.inference.api.text.gemini_generate import GeminiGenerateAPI
from program.inference.api.text.mistral_chat import MistralChatAPI
from program.inference.api.text.ollama_chat import OllamaChatAPI
from program.inference.provider.oauth.openai_codex import OpenAICodexOAuthProvider
from program.inference.provider.oauth.anthropic_claude_code import AnthropicClaudeCodeOAuthProvider
from program.inference.provider.oauth.github_copilot import GitHubCopilotOAuthProvider
from program.inference.provider.oauth.google_antigravity import GoogleAntigravityOAuthProvider
from program.inference.provider.types import APIProvider, OAuthProvider, ImageProvider, AudioProvider
from program.inference.types import LLMOptions

LLM_API_PROVIDERS: list[APIProvider] = [
    APIProvider(id="openai",     name="OpenAI",     api=OpenAIResponsesAPI,  options=LLMOptions()),
    APIProvider(id="anthropic",  name="Anthropic",  api=AnthropicMessagesAPI, options=LLMOptions()),
    APIProvider(id="google",     name="Google",     api=GeminiGenerateAPI,   options=LLMOptions()),
    APIProvider(id="nvidia",     name="NVIDIA",     api=OpenAICompletionsAPI, options=LLMOptions(base_url="https://integrate.api.nvidia.com/v1")),
    APIProvider(id="groq",       name="Groq",       api=OpenAICompletionsAPI, options=LLMOptions(base_url="https://api.groq.com/openai/v1")),
    APIProvider(id="mistral",    name="Mistral",    api=MistralChatAPI,      options=LLMOptions()),
    APIProvider(id="ollama",     name="Ollama",     api=OllamaChatAPI,       options=LLMOptions(base_url="http://localhost:11434")),
]

LLM_OAUTH_PROVIDERS: list[OAuthProvider] = [
    OpenAICodexOAuthProvider(),
    AnthropicClaudeCodeOAuthProvider(),
    GitHubCopilotOAuthProvider(),
    GoogleAntigravityOAuthProvider(),
]

LLM_PROVIDERS = LLM_API_PROVIDERS + LLM_OAUTH_PROVIDERS

IMAGE_PROVIDERS: list[ImageProvider] = [
    ImageProvider(name="openrouter", api="openrouter-images", base_url="https://openrouter.ai/api/v1"),
]

AUDIO_PROVIDERS: list[AudioProvider] = [
    AudioProvider(name="openai",     api="openai-audio"),
    AudioProvider(name="groq",       api="openai-audio",     base_url="https://api.groq.com/openai/v1"),
    AudioProvider(name="google",     api="gemini-audio"),
    AudioProvider(name="sarvam",     api="sarvam-audio",     base_url="https://api.sarvam.ai"),
    AudioProvider(name="elevenlabs", api="elevenlabs-audio", base_url="https://api.elevenlabs.io"),
]

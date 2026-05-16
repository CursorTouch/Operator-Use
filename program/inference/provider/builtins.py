from program.inference.api.llm.openai_responses import OpenAIResponsesAPI
from program.inference.api.llm.openai_completions import OpenAICompletionsAPI
from program.inference.api.llm.anthropic_messages import AnthropicMessagesAPI
from program.inference.api.llm.gemini_generate import GeminiGenerateAPI
from program.inference.api.llm.mistral_chat import MistralChatAPI
from program.inference.api.llm.ollama_chat import OllamaChatAPI
from program.inference.provider.oauth.openai_codex import OpenAICodexOAuthProvider
from program.inference.provider.oauth.anthropic_claude_code import AnthropicClaudeCodeOAuthProvider
from program.inference.provider.oauth.github_copilot import GitHubCopilotOAuthProvider
from program.inference.provider.oauth.google_antigravity import GoogleAntigravityOAuthProvider
from program.inference.provider.types import APIProvider, OAuthProvider, ImageProvider
from program.inference.types import Options

LLM_API_PROVIDERS: list[APIProvider] = [
    APIProvider(id="openai",     name="OpenAI",     api=OpenAIResponsesAPI,  options=Options()),
    APIProvider(id="anthropic",  name="Anthropic",  api=AnthropicMessagesAPI, options=Options()),
    APIProvider(id="google",     name="Google",     api=GeminiGenerateAPI,   options=Options()),
    APIProvider(id="nvidia",     name="NVIDIA",     api=OpenAICompletionsAPI, options=Options(base_url="https://integrate.api.nvidia.com/v1")),
    APIProvider(id="mistral",    name="Mistral",    api=MistralChatAPI,      options=Options()),
    APIProvider(id="ollama",     name="Ollama",     api=OllamaChatAPI,       options=Options(base_url="http://localhost:11434")),
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

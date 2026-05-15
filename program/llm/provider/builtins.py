from program.llm.api.openai_responses import OpenAIResponsesAPI
from program.llm.api.openai_completions import OpenAICompletionsAPI
from program.llm.api.anthropic_messages import AnthropicMessagesAPI
from program.llm.api.gemini_generate import GeminiGenerateAPI
from program.llm.api.mistral_chat import MistralChatAPI
from program.llm.api.ollama_chat import OllamaChatAPI
from program.llm.provider.oauth.openai_codex import OpenAICodexOAuthProvider
from program.llm.provider.oauth.anthropic_claude_code import AnthropicClaudeCodeOAuthProvider
from program.llm.provider.oauth.github_copilot import GitHubCopilotOAuthProvider
from program.llm.provider.oauth.google_antigravity import GoogleAntigravityOAuthProvider
from program.llm.provider.types import APIProvider, OAuthProvider
from program.llm.types import Options

API_PROVIDERS: list[APIProvider] = [
    APIProvider(
        id="openai",
        name="OpenAI",
        api=OpenAIResponsesAPI,
        options=Options(),
    ),
    APIProvider(
        id="anthropic",
        name="Anthropic",
        api=AnthropicMessagesAPI,
        options=Options(),
    ),
    APIProvider(
        id="google",
        name="Google",
        api=GeminiGenerateAPI,
        options=Options(),
    ),
    APIProvider(
        id="nvidia",
        name="NVIDIA",
        api=OpenAICompletionsAPI,
        options=Options(base_url="https://integrate.api.nvidia.com/v1"),
    ),
    APIProvider(
        id="mistral",
        name="Mistral",
        api=MistralChatAPI,
        options=Options(),
    ),
    APIProvider(
        id="ollama",
        name="Ollama",
        api=OllamaChatAPI,
        options=Options(base_url="http://localhost:11434"),
    ),
]

OAUTH_PROVIDERS: list[OAuthProvider] = [
    OpenAICodexOAuthProvider(),
    AnthropicClaudeCodeOAuthProvider(),
    GitHubCopilotOAuthProvider(),
    GoogleAntigravityOAuthProvider(),
]

PROVIDERS = API_PROVIDERS + OAUTH_PROVIDERS

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
from program.inference.provider.types import APIProvider, OAuthProvider
from program.inference.types import LLMOptions

api_providers = [
    APIProvider(id="openai",    name="OpenAI",    api=OpenAIResponsesAPI,   options=LLMOptions()),
    APIProvider(id="anthropic", name="Anthropic", api=AnthropicMessagesAPI, options=LLMOptions()),
    APIProvider(id="google",    name="Google",    api=GeminiGenerateAPI,    options=LLMOptions()),
    APIProvider(id="nvidia",    name="NVIDIA",    api=OpenAICompletionsAPI, options=LLMOptions(base_url="https://integrate.api.nvidia.com/v1")),
    APIProvider(id="groq",      name="Groq",      api=OpenAICompletionsAPI, options=LLMOptions(base_url="https://api.groq.com/openai/v1")),
    APIProvider(id="mistral",   name="Mistral",   api=MistralChatAPI,       options=LLMOptions()),
    APIProvider(id="ollama",    name="Ollama",    api=OllamaChatAPI,        options=LLMOptions(base_url="http://localhost:11434")),
]

oauth_providers = [
    OpenAICodexOAuthProvider(),
    AnthropicClaudeCodeOAuthProvider(),
    GitHubCopilotOAuthProvider(),
    GoogleAntigravityOAuthProvider(),
]

providers = api_providers + oauth_providers

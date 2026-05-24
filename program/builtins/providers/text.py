from program.inference.provider.oauth.openai_codex import OpenAICodexOAuthProvider
from program.inference.provider.oauth.anthropic_claude_code import AnthropicClaudeCodeOAuthProvider
from program.inference.provider.oauth.github_copilot import GitHubCopilotOAuthProvider
from program.inference.provider.oauth.google_antigravity import GoogleAntigravityOAuthProvider
from program.inference.provider.types import APIProvider
from program.inference.types import LLMOptions

# Use registry name strings for `api` so each provider's SDK is imported lazily
# (resolved via LLMAPIRegistry on first use) instead of at startup.
api_providers = [
    APIProvider(id="openai",    name="OpenAI",    api="openai_responses",   options=LLMOptions()),
    APIProvider(id="anthropic", name="Anthropic", api="anthropic_messages", options=LLMOptions()),
    APIProvider(id="google",    name="Google",    api="gemini_generate",    options=LLMOptions()),
    APIProvider(id="nvidia",    name="NVIDIA",    api="openai_completions", options=LLMOptions(base_url="https://integrate.api.nvidia.com/v1")),
    APIProvider(id="groq",      name="Groq",      api="openai_completions", options=LLMOptions(base_url="https://api.groq.com/openai/v1")),
    APIProvider(id="openrouter",name="OpenRouter",api="openai_completions", options=LLMOptions(base_url="https://openrouter.ai/api/v1")),
    APIProvider(id="perplexity",name="Perplexity",api="openai_responses",   options=LLMOptions(base_url="https://api.perplexity.ai/v1")),
    APIProvider(id="xai",       name="xAI",       api="openai_responses",   options=LLMOptions(base_url="https://api.x.ai/v1")),
    APIProvider(id="bedrock",   name="AWS Bedrock", api="openai_responses", options=LLMOptions(base_url="https://bedrock-mantle.us-east-1.api.aws/v1")),
    APIProvider(id="kimi",      name="Kimi / Moonshot", api="openai_completions", options=LLMOptions(base_url="https://api.moonshot.ai/v1")),
    APIProvider(id="minimax",   name="MiniMax",   api="anthropic_messages", options=LLMOptions(base_url="https://api.minimax.io/anthropic")),
    APIProvider(id="deepseek",  name="DeepSeek",  api="openai_completions", options=LLMOptions(base_url="https://api.deepseek.com")),
    APIProvider(id="kilocode",  name="Kilo Code", api="openai_completions", options=LLMOptions(base_url="https://api.kilo.ai/api/gateway")),
    APIProvider(id="mistral",   name="Mistral",   api="mistral_chat",       options=LLMOptions()),
    APIProvider(id="ollama",    name="Ollama",    api="ollama_chat",        options=LLMOptions(base_url="http://localhost:11434")),
]

oauth_providers = [
    OpenAICodexOAuthProvider(),
    AnthropicClaudeCodeOAuthProvider(),
    GitHubCopilotOAuthProvider(),
    GoogleAntigravityOAuthProvider(),
]

providers = api_providers + oauth_providers

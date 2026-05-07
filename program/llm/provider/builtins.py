from program.llm.api.openai_responses import OpenAIResponsesAPI
from program.llm.api.openai_completions import OpenAICompletionsAPI
from program.llm.api.anthropic_messages import AnthropicMessagesAPI
from program.llm.api.gemini_generate import GeminiGenerateAPI
from program.llm.api.mistral_chat import MistralChatAPI
from program.llm.api.ollama_chat import OllamaChatAPI
from program.llm.provider.types import APIProvider
from program.llm.types import Options

PROVIDERS: list[APIProvider] = [
    APIProvider(
        name="openai",
        api=OpenAIResponsesAPI,
        options=Options(),
    ),
    APIProvider(
        name="anthropic",
        api=AnthropicMessagesAPI,
        options=Options(),
    ),
    APIProvider(
        name="google",
        api=GeminiGenerateAPI,
        options=Options(),
    ),
    APIProvider(
        name="nvidia",
        api=OpenAICompletionsAPI,
        options=Options(base_url="https://integrate.api.nvidia.com/v1"),
    ),
    APIProvider(
        name="mistral",
        api=MistralChatAPI,
        options=Options(),
    ),
    APIProvider(
        name="ollama",
        api=OllamaChatAPI,
        options=Options(base_url="http://localhost:11434"),
    ),
]

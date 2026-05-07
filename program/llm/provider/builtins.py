from program.llm.api.openai_responses import OpenAIResponsesAPI
from program.llm.api.openai_completions import OpenAICompletionsAPI
from program.llm.api.anthropic_messages import AnthropicMessagesAPI
from program.llm.api.gemini_generate import GeminiGenerateAPI
from program.llm.api.mistral_chat import MistralChatAPI
from program.llm.api.ollama_chat import OllamaChatAPI
from program.llm.provider.types import Provider
from program.llm.types import Options

PROVIDERS: list[Provider] = [
    Provider(
        name="openai",
        api=OpenAIResponsesAPI,
        options=Options(),
    ),
    Provider(
        name="anthropic",
        api=AnthropicMessagesAPI,
        options=Options(),
    ),
    Provider(
        name="google",
        api=GeminiGenerateAPI,
        options=Options(),
    ),
    Provider(
        name="nvidia",
        api=OpenAICompletionsAPI,
        options=Options(base_url="https://integrate.api.nvidia.com/v1"),
    ),
    Provider(
        name="mistral",
        api=MistralChatAPI,
        options=Options(),
    ),
    Provider(
        name="ollama",
        api=OllamaChatAPI,
        options=Options(base_url="http://localhost:11434"),
    ),
]

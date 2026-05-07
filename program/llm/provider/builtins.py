from program.llm.api.openai_responses import OpenAIResponsesAPI
from program.llm.api.gemini_generate import GeminiGenerateAPI
from program.llm.provider.types import Provider
from program.llm.types import Options

PROVIDERS: list[Provider] = [
    Provider(
        name="openai",
        api=OpenAIResponsesAPI,
        options=Options(),
    ),
    Provider(
        name="google",
        api=GeminiGenerateAPI,
        options=Options(),
    ),
]

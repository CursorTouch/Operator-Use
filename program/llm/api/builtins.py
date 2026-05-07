from program.llm.api.openai_responses import OpenAIResponsesAPI
from program.llm.api.openai_completions import OpenAICompletionsAPI
from program.llm.api.openai_codex_responses import OpenAICodexResponsesAPI
from program.llm.api.anthropic_messages import AnthropicMessagesAPI
from program.llm.api.anthropic_claude_code import AnthropicClaudeCodeAPI
from program.llm.api.github_copilot_chat import GitHubCopilotChatAPI
from program.llm.api.gemini_generate import GeminiGenerateAPI
from program.llm.api.mistral_chat import MistralChatAPI
from program.llm.api.ollama_chat import OllamaChatAPI
from program.llm.api.google_antigravity import GoogleAntigravityAPI
from program.llm.api.base import BaseAPI
from typing import Type

APIS: list[tuple[str, Type[BaseAPI]]] = [
    ("openai_responses", OpenAIResponsesAPI),
    ("openai_completions", OpenAICompletionsAPI),
    ("openai_codex_responses", OpenAICodexResponsesAPI),
    ("anthropic_messages", AnthropicMessagesAPI),
    ("anthropic_claude_code", AnthropicClaudeCodeAPI),
    ("github_copilot_chat", GitHubCopilotChatAPI),
    ("gemini_generate", GeminiGenerateAPI),
    ("mistral_chat", MistralChatAPI),
    ("ollama_chat", OllamaChatAPI),
    ("google_antigravity", GoogleAntigravityAPI),
]

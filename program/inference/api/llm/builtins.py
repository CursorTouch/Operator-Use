from program.inference.api.llm.openai_responses import OpenAIResponsesAPI
from program.inference.api.llm.openai_completions import OpenAICompletionsAPI
from program.inference.api.llm.openai_codex_responses import OpenAICodexResponsesAPI
from program.inference.api.llm.anthropic_messages import AnthropicMessagesAPI
from program.inference.api.llm.anthropic_claude_code import AnthropicClaudeCodeAPI
from program.inference.api.llm.github_copilot_chat import GitHubCopilotChatAPI
from program.inference.api.llm.gemini_generate import GeminiGenerateAPI
from program.inference.api.llm.mistral_chat import MistralChatAPI
from program.inference.api.llm.ollama_chat import OllamaChatAPI
from program.inference.api.llm.google_antigravity import GoogleAntigravityAPI
from program.inference.api.llm.base import BaseLLMAPI
from typing import Type

LLM_APIS: list[tuple[str, Type[BaseLLMAPI]]] = [
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

# Backward-compat alias
APIS = LLM_APIS

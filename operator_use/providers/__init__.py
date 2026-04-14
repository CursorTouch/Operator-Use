"""
Unified provider package for Windows-Use.

Each provider lives in its own sub-package (e.g. ``providers.google``)
and exposes all capabilities (LLM, STT, TTS) it supports.

Shared base protocols and data models:
    - ``BaseChatLLM``  — LLM provider protocol
    - ``BaseSTT``      — Speech-to-Text provider protocol
    - ``BaseTTS``      — Text-to-Speech provider protocol
    - ``TokenUsage``, ``Metadata`` — LLM data models
"""

# Base protocols & data models
from operator_use.providers.base import BaseChatLLM, BaseSTT, BaseTTS, BaseImage, BaseSearch
from operator_use.providers.views import TokenUsage, Metadata
from operator_use.providers.events import Thinking, LLMEvent, LLMStreamEvent, ToolCall

# LLM providers
from operator_use.providers.anthropic import ChatAnthropic
from operator_use.providers.google import ChatGoogle
from operator_use.providers.openai import ChatOpenAI
from operator_use.providers.ollama import ChatOllama
from operator_use.providers.groq import ChatGroq
from operator_use.providers.mistral import ChatMistral
from operator_use.providers.cerebras import ChatCerebras
from operator_use.providers.open_router import ChatOpenRouter
from operator_use.providers.azure_openai import ChatAzureOpenAI
from operator_use.providers.vllm import ChatVLLM
from operator_use.providers.nvidia import ChatNvidia
from operator_use.providers.deepseek import ChatDeepSeek
from operator_use.providers.xai import ChatXai, TTSXai, ImageXai
from operator_use.providers.zai import ChatZAI

try:
    from operator_use.providers.codex import ChatCodex
except ImportError:
    pass

try:
    from operator_use.providers.claude_code import ChatClaudeCode
except ImportError:
    pass

try:
    from operator_use.providers.antigravity import ChatAntigravity
except ImportError:
    pass

try:
    from operator_use.providers.github_copilot import ChatGitHubCopilot
except ImportError:
    pass

# STT providers
from operator_use.providers.openai import STTOpenAI
from operator_use.providers.google import STTGoogle
from operator_use.providers.groq import STTGroq

try:
    from operator_use.providers.elevenlabs import STTElevenLabs
except ImportError:
    pass

try:
    from operator_use.providers.deepgram import STTDeepgram
except ImportError:
    pass

try:
    from operator_use.providers.sarvam import STTSarvam
except ImportError:
    pass

# TTS providers
from operator_use.providers.openai import TTSOpenAI
from operator_use.providers.google import TTSGoogle

# Image generation providers
from operator_use.providers.openai import ImageOpenAI
from operator_use.providers.google import ImageGoogle

try:
    from operator_use.providers.together import ImageTogether
except ImportError:
    pass

try:
    from operator_use.providers.fal import ImageFal
except ImportError:
    pass
from operator_use.providers.groq import TTSGroq

try:
    from operator_use.providers.elevenlabs import TTSElevenLabs
except ImportError:
    pass

try:
    from operator_use.providers.deepgram import TTSDeepgram
except ImportError:
    pass

try:
    from operator_use.providers.sarvam import TTSSarvam
except ImportError:
    pass

# Search providers
from operator_use.providers.ddgs import DDGSSearch

try:
    from operator_use.providers.exa import ExaSearch
except ImportError:
    pass

try:
    from operator_use.providers.tavily import TavilySearch
except ImportError:
    pass

# Misc
from operator_use.providers.google.tts import GOOGLE_TTS_VOICES

__all__ = [
    # Base
    "BaseChatLLM",
    "BaseSTT",
    "BaseTTS",
    "BaseImage",
    "BaseSearch",
    "TokenUsage",
    "Metadata",
    "Thinking",
    "LLMEvent",
    "LLMStreamEvent",
    "ToolCall",
    # LLM providers
    "ChatAnthropic",
    "ChatGoogle",
    "ChatOpenAI",
    "ChatOllama",
    "ChatGroq",
    "ChatMistral",
    "ChatCerebras",
    "ChatOpenRouter",
    "ChatAzureOpenAI",
    "ChatVLLM",
    "ChatNvidia",
    "ChatDeepSeek",
    "ChatXai",
    "ChatZAI",
    "ChatCodex",
    "ChatClaudeCode",
    "ChatAntigravity",
    "ChatGitHubCopilot",
    # STT providers
    "STTOpenAI",
    "STTGoogle",
    "STTGroq",
    "STTElevenLabs",
    "STTDeepgram",
    "STTSarvam",
    # TTS providers
    "TTSOpenAI",
    "TTSGoogle",
    "TTSGroq",
    "TTSXai",
    "TTSElevenLabs",
    "TTSDeepgram",
    "TTSSarvam",
    "GOOGLE_TTS_VOICES",
    # Image generation providers
    "ImageOpenAI",
    "ImageGoogle",
    "ImageXai",
    "ImageTogether",
    "ImageFal",
    # Search providers
    "DDGSSearch",
    "ExaSearch",
    "TavilySearch",
]

"""DeepSeek LLM provider via OpenAI-compatible API."""

import os
from typing import Optional

from operator_use.providers.openai.llm import ChatOpenAI
from operator_use.providers.views import Metadata

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"


class ChatDeepSeek(ChatOpenAI):
    """
    DeepSeek LLM implementation using the OpenAI client.

    Supports deepseek-chat and deepseek-reasoner (with thinking).
    Set DEEPSEEK_API_KEY in the environment.
    """

    # Available models with context windows (tokens)
    # Source: https://api-docs.deepseek.com/quick_start/pricing
    MODELS = {
        "deepseek-chat": 128000,  # DeepSeek V3.2 (no reasoning)
        "deepseek-reasoner": 128000,  # DeepSeek V3.2 (reasoning/thinking)
        "deepseek-r1": 128000,  # DeepSeek R1 (reasoning)
        "deepseek-v3": 128000,  # DeepSeek V3
    }

    # Models that support chain-of-thought reasoning
    REASONING_PATTERNS = ("reasoner", "r1")

    def __init__(
        self,
        model: str = "deepseek-chat",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 600.0,
        max_retries: int = 2,
        temperature: Optional[float] = None,
        **kwargs,
    ):
        api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        base_url = base_url or os.environ.get("DEEPSEEK_API_BASE") or DEEPSEEK_BASE_URL
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            temperature=temperature,
            **kwargs,
        )

    @property
    def provider(self) -> str:
        return "deepseek"

    def _is_reasoning_model(self) -> bool:
        """DeepSeek reasoner/r1 models support thinking/reasoning_content."""
        return any(p in self._model for p in self.REASONING_PATTERNS)

    def get_metadata(self) -> Metadata:
        context_window = self.MODELS.get(self._model, 128000)
        return Metadata(name=self._model, context_window=context_window, owned_by="deepseek")

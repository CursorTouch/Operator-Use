"""xAI Grok LLM provider via OpenAI-compatible API."""

import os
from typing import Optional

from operator_use.providers.openai.llm import ChatOpenAI
from operator_use.providers.views import Metadata

XAI_BASE_URL = "https://api.x.ai/v1"


class ChatXai(ChatOpenAI):
    """
    xAI Grok LLM implementation using the OpenAI-compatible client.

    Uses xAI's OpenAI-compatible API.
    Set XAI_API_KEY in the environment.
    """

    # Available models with context windows (tokens)
    # Source: https://docs.x.ai/docs/models
    MODELS = {
        "grok-4": 2000000,  # Grok 4 (flagship, reasoning)
        "grok-3": 2000000,  # Grok 3
        "grok-3-mini": 2000000,  # Grok 3 Mini (reasoning)
        "grok-3-fast": 2000000,  # Grok 3 Fast
        "grok-3-mini-fast": 2000000,  # Grok 3 Mini Fast (reasoning)
    }

    # Models that support chain-of-thought reasoning
    REASONING_PATTERNS = (
        "grok-4",
        "grok-3-mini",
    )

    def __init__(
        self,
        model: str = "grok-4",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 600.0,
        max_retries: int = 2,
        temperature: Optional[float] = None,
        **kwargs,
    ):
        api_key = api_key or os.environ.get("XAI_API_KEY")
        base_url = base_url or os.environ.get("XAI_API_BASE") or XAI_BASE_URL
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
        return "xai"

    def _is_reasoning_model(self) -> bool:
        return any(p in self._model for p in self.REASONING_PATTERNS)

    def get_metadata(self) -> Metadata:
        context_window = self.MODELS.get(self._model, 2000000)
        return Metadata(
            name=self._model,
            context_window=context_window,
            owned_by="xai",
        )

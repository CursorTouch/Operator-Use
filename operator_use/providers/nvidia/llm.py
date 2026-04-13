"""NVIDIA NIM LLM provider via OpenAI-compatible API."""

import os
from typing import Optional

from operator_use.providers.openai.llm import ChatOpenAI
from operator_use.providers.views import Metadata

NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"


class ChatNvidia(ChatOpenAI):
    """
    NVIDIA NIM LLM implementation using the OpenAI client.

    Uses NVIDIA's OpenAI-compatible NIM API.
    Set NVIDIA_NIM_API_KEY or NVIDIA_API_KEY in the environment.
    """

    # Available models with context windows (tokens)
    MODELS = {
        # NVIDIA native models
        "nvidia/nemotron-3-super-120b-a12b": 128000,  # Hybrid MoE LLM, agentic reasoning
        # Qwen models
        "qwen/qwen3.5-122b-a10b": 131072,  # 122B MoE LLM for coding/reasoning
        "qwen/qwen3.5-397b-a17b": 131072,  # Next-gen VLM 400B MoE
        # Z-AI (Zhipu) models
        "z-ai/glm-5": 131072,  # 744B MoE reasoning model
        "z-ai/glm-4.7": 131072,  # Multilingual agentic coding
        # MiniMax models
        "minimaxai/minimax-m2.5": 40960,  # 230B text-to-text coding/reasoning
        # StepFun models
        "stepfun-ai/step-3.5-flash": 200000,  # 200B reasoning engine
        # Moonshot models
        "moonshotai/kimi-k2.5": 1048576,  # 1T multimodal MoE
    }

    # Models that support extended reasoning/thinking
    REASONING_PATTERNS = (
        "nemotron-3-super",
        "glm-5",
        "step-3.5",
        "kimi-k2",
    )

    def __init__(
        self,
        model: str = "qwen/qwen3.5-122b-a10b",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 600.0,
        max_retries: int = 2,
        temperature: Optional[float] = None,
        **kwargs,
    ):
        api_key = (
            api_key or os.environ.get("NVIDIA_NIM_API_KEY") or os.environ.get("NVIDIA_API_KEY")
        )
        base_url = base_url or os.environ.get("NVIDIA_NIM_API_BASE") or NVIDIA_NIM_BASE_URL
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
        return "nvidia"

    def _is_reasoning_model(self) -> bool:
        return any(p in self._model for p in self.REASONING_PATTERNS)

    def get_metadata(self) -> Metadata:
        context_window = self.MODELS.get(self._model, 131072)
        return Metadata(
            name=self._model,
            context_window=context_window,
            owned_by="nvidia",
        )

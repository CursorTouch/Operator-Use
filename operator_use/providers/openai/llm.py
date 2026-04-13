import os
import json
import logging
from typing import Iterator, AsyncIterator, List, Optional, Any, overload
from openai import OpenAI, AsyncOpenAI
from pydantic import BaseModel
from operator_use.providers.base import BaseChatLLM
from operator_use.providers.views import TokenUsage, Metadata
from operator_use.messages import (
    BaseMessage,
    SystemMessage,
    HumanMessage,
    AIMessage,
    ImageMessage,
    ToolMessage,
)
from operator_use.tools import Tool
from operator_use.providers.events import (
    LLMEvent,
    LLMEventType,
    LLMStreamEvent,
    LLMStreamEventType,
    ToolCall,
    Thinking,
)

logger = logging.getLogger(__name__)


class ChatOpenAI(BaseChatLLM):
    """
    OpenAI LLM implementation following the BaseChatLLM protocol.

    Supports:
    - Standard chat completions
    - Tool/function calling
    - Structured outputs (via response_format)
    - Streaming
    - Vision (image inputs)
    - Reasoning models (o1, o3, o4 series)
    """

    # Available models with context windows (tokens)
    # Source: https://platform.openai.com/docs/models
    MODELS = {
        # GPT-5.4 series
        "gpt-5.4": 1050000,  # GPT-5.4 flagship
        "gpt-5.4-mini": 400000,  # GPT-5.4 Mini
        "gpt-5.4-nano": 400000,  # GPT-5.4 Nano
        # GPT-5.2 series
        "gpt-5.2": 400000,  # GPT-5.2
        # GPT-4.1 series
        "gpt-4.1": 1000000,  # GPT-4.1
        "gpt-4.1-mini": 1000000,  # GPT-4.1 Mini
        "gpt-4.1-nano": 1000000,  # GPT-4.1 Nano
        # GPT-4o series
        "gpt-4o": 128000,  # GPT-4o
        "gpt-4o-mini": 128000,  # GPT-4o Mini
        # o-series (reasoning)
        "o1": 200000,  # o1
        "o3": 200000,  # o3
        "o3-mini": 200000,  # o3 Mini
        "o3-pro": 200000,  # o3 Pro
        "o4-mini": 200000,  # o4 Mini
    }

    # Models that support chain-of-thought reasoning
    REASONING_PATTERNS = ("o1", "o3", "o4")

    def __init__(
        self,
        model: str = "gpt-4.1",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 600.0,
        max_retries: int = 2,
        temperature: Optional[float] = None,
        **kwargs,
    ):
        """
        Initialize the OpenAI LLM.

        Args:
            model (str): The model name to use. Defaults to "gpt-4o".
            api_key (str, optional): OpenAI API key. Defaults to OPENAI_API_KEY environment variable.
            base_url (str, optional): Base URL for the API. Defaults to OPENAI_BASE_URL environment variable.
            timeout (float): Request timeout in seconds.
            max_retries (int): Maximum number of retries for failed requests.
            temperature (float, optional): Sampling temperature.
            **kwargs: Additional arguments to pass to the OpenAI chat completions create method.
        """
        self._model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        self.temperature = temperature

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=timeout,
            max_retries=max_retries,
        )
        self.aclient = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=timeout,
            max_retries=max_retries,
        )
        self.kwargs = kwargs

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "openai"

    def _is_reasoning_model(self) -> bool:
        """Check if the model is a reasoning model (o-series: o1, o3, o4, etc.)."""
        return self._model.startswith(self.REASONING_PATTERNS)

    def _convert_messages(self, messages: List[BaseMessage]) -> List[dict]:
        """
        Convert BaseMessage objects to OpenAI-compatible message dictionaries.
        """
        openai_messages = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                openai_messages.append({"role": "system", "content": msg.content})
            elif isinstance(msg, HumanMessage):
                openai_messages.append({"role": "user", "content": msg.content})
            elif isinstance(msg, ImageMessage):
                content_list = []
                if msg.content:
                    content_list.append({"type": "text", "text": msg.content})

                b64_imgs = msg.convert_images(format="base64")
                for b64 in b64_imgs:
                    content_list.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{msg.mime_type};base64,{b64}"},
                        }
                    )
                openai_messages.append({"role": "user", "content": content_list})
            elif isinstance(msg, AIMessage):
                # Handle AIMessage with potential thinking content
                content = msg.content
                msg_dict: dict = {"role": "assistant", "content": content}
                # For o1/o3/o4 models, pass reasoning_content so the model can continue its
                # reasoning chain in multi-turn conversations (especially with tool calls).
                if self._is_reasoning_model() and getattr(msg, "thinking", None):
                    msg_dict["reasoning_content"] = msg.thinking
                openai_messages.append(msg_dict)
            elif isinstance(msg, ToolMessage):
                # Reconstruct the tool call and the result for history consistency
                tool_call = {
                    "id": msg.id,
                    "type": "function",
                    "function": {"name": msg.name, "arguments": json.dumps(msg.params)},
                }
                openai_messages.append(
                    {"role": "assistant", "content": None, "tool_calls": [tool_call]}
                )
                openai_messages.append(
                    {"role": "tool", "tool_call_id": msg.id, "content": msg.content or ""}
                )
        return openai_messages

    def _convert_tools(self, tools: List[Tool]) -> List[dict]:
        """
        Convert Tool objects to OpenAI-compatible tool definitions.
        """
        return [{"type": "function", "function": tool.json_schema} for tool in tools]

    def _process_response(self, response: Any) -> LLMEvent:
        """
        Process OpenAI API response into AIMessage or ToolMessage.
        """
        choice = response.choices[0]
        message = choice.message
        usage_data = response.usage

        # Capture reasoning tokens if available (o1 models)
        thinking_tokens = None
        if (
            hasattr(usage_data, "completion_tokens_details")
            and usage_data.completion_tokens_details
        ):
            thinking_tokens = getattr(
                usage_data.completion_tokens_details, "reasoning_tokens", None
            ) or getattr(usage_data.completion_tokens_details, "thinking_tokens", None)
            if thinking_tokens is not None:
                logger.debug(f"Reasoning tokens used: {thinking_tokens}")

        usage = TokenUsage(
            prompt_tokens=usage_data.prompt_tokens,
            completion_tokens=usage_data.completion_tokens,
            total_tokens=usage_data.total_tokens,
            thinking_tokens=thinking_tokens,
        )

        # Extract thinking/reasoning content (for o1 models)
        thinking = None
        if self._is_reasoning_model():
            if hasattr(message, "reasoning_content"):
                thinking = message.reasoning_content
            elif hasattr(choice, "reasoning_content"):
                thinking = choice.reasoning_content

        thinking_obj = Thinking(content=thinking, signature=None) if thinking else None

        if message.tool_calls:
            tool_call = message.tool_calls[0]
            try:
                params = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                params = {}
            return LLMEvent(
                type=LLMEventType.TOOL_CALL,
                tool_call=ToolCall(id=tool_call.id, name=tool_call.function.name, params=params),
                usage=usage,
            )
        return LLMEvent(
            type=LLMEventType.TEXT,
            content=message.content or "",
            thinking=thinking_obj,
            usage=usage,
        )

    @overload
    def invoke(
        self,
        messages: list[BaseMessage],
        tools: list[Tool] = [],
        structured_output: BaseModel | None = None,
        json_mode: bool = False,
    ) -> LLMEvent: ...

    def invoke(
        self,
        messages: list[BaseMessage],
        tools: list[Tool] = [],
        structured_output: BaseModel | None = None,
        json_mode: bool = False,
    ) -> LLMEvent:
        openai_messages = self._convert_messages(messages)
        openai_tools = self._convert_tools(tools) if tools else None

        params = {"model": self._model, "messages": openai_messages, **self.kwargs}

        # Only add tools if they exist
        if openai_tools:
            params["tools"] = openai_tools

        # Temperature handling - o1 models don't support temperature
        if self.temperature is not None and not self._is_reasoning_model():
            params["temperature"] = self.temperature

        if structured_output:
            # Use beta parse endpoint for structured outputs
            response = self.client.beta.chat.completions.parse(
                **params,
                response_format=structured_output,
            )

            thinking_tokens = None
            if (
                hasattr(response.usage, "completion_tokens_details")
                and response.usage.completion_tokens_details
            ):
                thinking_tokens = getattr(
                    response.usage.completion_tokens_details, "reasoning_tokens", None
                ) or getattr(response.usage.completion_tokens_details, "thinking_tokens", None)

            TokenUsage(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
                thinking_tokens=thinking_tokens,
            )
            parsed = response.choices[0].message.parsed
            content = parsed.model_dump() if hasattr(parsed, "model_dump") else str(parsed)
            return LLMEvent(
                type=LLMEventType.TEXT,
                content=json.dumps(content) if isinstance(content, dict) else content,
            )

        if json_mode:
            params["response_format"] = {"type": "json_object"}

        response = self.client.chat.completions.create(**params)
        return self._process_response(response)

    @overload
    async def ainvoke(
        self,
        messages: list[BaseMessage],
        tools: list[Tool] = [],
        structured_output: BaseModel | None = None,
        json_mode: bool = False,
    ) -> LLMEvent: ...

    async def ainvoke(
        self,
        messages: list[BaseMessage],
        tools: list[Tool] = [],
        structured_output: BaseModel | None = None,
        json_mode: bool = False,
    ) -> LLMEvent:
        openai_messages = self._convert_messages(messages)
        openai_tools = self._convert_tools(tools) if tools else None

        params = {"model": self._model, "messages": openai_messages, **self.kwargs}

        if openai_tools:
            params["tools"] = openai_tools

        if self.temperature is not None and not self._is_reasoning_model():
            params["temperature"] = self.temperature

        if structured_output:
            response = await self.aclient.beta.chat.completions.parse(
                **params,
                response_format=structured_output,
            )

            thinking_tokens = None
            if (
                hasattr(response.usage, "completion_tokens_details")
                and response.usage.completion_tokens_details
            ):
                thinking_tokens = getattr(
                    response.usage.completion_tokens_details, "reasoning_tokens", None
                ) or getattr(response.usage.completion_tokens_details, "thinking_tokens", None)

            usage = TokenUsage(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
                thinking_tokens=thinking_tokens,
            )
            parsed = response.choices[0].message.parsed
            content = parsed.model_dump() if hasattr(parsed, "model_dump") else str(parsed)
            return LLMEvent(
                type=LLMEventType.TEXT,
                content=json.dumps(content) if isinstance(content, dict) else content,
                usage=usage,
            )

        if json_mode:
            params["response_format"] = {"type": "json_object"}

        response = await self.aclient.chat.completions.create(**params)
        return self._process_response(response)

    @overload
    def stream(
        self,
        messages: list[BaseMessage],
        tools: list[Tool] = [],
        structured_output: BaseModel | None = None,
        json_mode: bool = False,
    ) -> Iterator[LLMStreamEvent]: ...

    def stream(
        self,
        messages: list[BaseMessage],
        tools: list[Tool] = [],
        structured_output: BaseModel | None = None,
        json_mode: bool = False,
    ) -> Iterator[LLMStreamEvent]:
        openai_messages = self._convert_messages(messages)
        openai_tools = self._convert_tools(tools) if tools else None

        params = {
            "model": self._model,
            "messages": openai_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            **self.kwargs,
        }

        if openai_tools:
            params["tools"] = openai_tools

        if self.temperature is not None and not self._is_reasoning_model():
            params["temperature"] = self.temperature

        if json_mode:
            params["response_format"] = {"type": "json_object"}

        response = self.client.chat.completions.create(**params)

        # Accumulators for streamed tool calls
        tool_call_id = None
        tool_call_name = None
        tool_call_args = ""
        usage = None

        text_started = False
        think_started = False
        usage = None

        for chunk in response:
            if not chunk.choices:
                # Final chunk with usage
                if chunk.usage:
                    thinking_tokens = None
                    if (
                        hasattr(chunk.usage, "completion_tokens_details")
                        and chunk.usage.completion_tokens_details
                    ):
                        thinking_tokens = getattr(
                            chunk.usage.completion_tokens_details, "reasoning_tokens", None
                        ) or getattr(chunk.usage.completion_tokens_details, "thinking_tokens", None)

                    usage = TokenUsage(
                        prompt_tokens=chunk.usage.prompt_tokens,
                        completion_tokens=chunk.usage.completion_tokens,
                        total_tokens=chunk.usage.total_tokens,
                        thinking_tokens=thinking_tokens,
                    )
                continue

            delta = chunk.choices[0].delta

            if (
                self._is_reasoning_model()
                and hasattr(delta, "reasoning_content")
                and delta.reasoning_content
            ):
                if not think_started:
                    think_started = True
                    yield LLMStreamEvent(type=LLMStreamEventType.THINK_START)
                yield LLMStreamEvent(
                    type=LLMStreamEventType.THINK_DELTA, content=delta.reasoning_content
                )

            if delta.content:
                if think_started:
                    yield LLMStreamEvent(type=LLMStreamEventType.THINK_END)
                    think_started = False
                if not text_started:
                    text_started = True
                    yield LLMStreamEvent(type=LLMStreamEventType.TEXT_START)
                yield LLMStreamEvent(type=LLMStreamEventType.TEXT_DELTA, content=delta.content)

            # Accumulate tool call deltas
            if hasattr(delta, "tool_calls") and delta.tool_calls:
                tc_delta = delta.tool_calls[0]
                if tc_delta.id:
                    tool_call_id = tc_delta.id

                if tc_delta.function:
                    if tc_delta.function.name:
                        tool_call_name = tc_delta.function.name
                    if tc_delta.function.arguments:
                        tool_call_args += tc_delta.function.arguments

        if think_started:
            yield LLMStreamEvent(type=LLMStreamEventType.THINK_END)

        # Yield accumulated tool call as final response (mutually exclusive with TEXT_END)
        if tool_call_id and tool_call_name:
            try:
                tool_params = json.loads(tool_call_args)
            except json.JSONDecodeError:
                tool_params = {}

            yield LLMStreamEvent(
                type=LLMStreamEventType.TOOL_CALL,
                tool_call=ToolCall(id=tool_call_id, name=tool_call_name, params=tool_params),
                usage=usage,
            )
        elif text_started:
            yield LLMStreamEvent(type=LLMStreamEventType.TEXT_END, usage=usage)

    @overload
    async def astream(
        self,
        messages: list[BaseMessage],
        tools: list[Tool] = [],
        structured_output: BaseModel | None = None,
        json_mode: bool = False,
    ) -> AsyncIterator[LLMStreamEvent]: ...

    async def astream(
        self,
        messages: list[BaseMessage],
        tools: list[Tool] = [],
        structured_output: BaseModel | None = None,
        json_mode: bool = False,
    ) -> AsyncIterator[LLMStreamEvent]:
        openai_messages = self._convert_messages(messages)
        openai_tools = self._convert_tools(tools) if tools else None

        params = {
            "model": self._model,
            "messages": openai_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            **self.kwargs,
        }

        if openai_tools:
            params["tools"] = openai_tools

        if self.temperature is not None and not self._is_reasoning_model():
            params["temperature"] = self.temperature

        if json_mode:
            params["response_format"] = {"type": "json_object"}

        response = await self.aclient.chat.completions.create(**params)

        # Accumulators for streamed tool calls
        tool_call_id = None
        tool_call_name = None
        tool_call_args = ""
        usage = None

        text_started = False
        think_started = False

        async for chunk in response:
            if not chunk.choices:
                # Final chunk with usage
                if chunk.usage:
                    thinking_tokens = None
                    if (
                        hasattr(chunk.usage, "completion_tokens_details")
                        and chunk.usage.completion_tokens_details
                    ):
                        thinking_tokens = getattr(
                            chunk.usage.completion_tokens_details, "reasoning_tokens", None
                        ) or getattr(chunk.usage.completion_tokens_details, "thinking_tokens", None)

                    usage = TokenUsage(
                        prompt_tokens=chunk.usage.prompt_tokens,
                        completion_tokens=chunk.usage.completion_tokens,
                        total_tokens=chunk.usage.total_tokens,
                        thinking_tokens=thinking_tokens,
                    )
                continue

            delta = chunk.choices[0].delta

            if (
                self._is_reasoning_model()
                and hasattr(delta, "reasoning_content")
                and delta.reasoning_content
            ):
                if not think_started:
                    think_started = True
                    yield LLMStreamEvent(type=LLMStreamEventType.THINK_START)
                yield LLMStreamEvent(
                    type=LLMStreamEventType.THINK_DELTA, content=delta.reasoning_content
                )

            if delta.content:
                if think_started:
                    yield LLMStreamEvent(type=LLMStreamEventType.THINK_END)
                    think_started = False
                if not text_started:
                    text_started = True
                    yield LLMStreamEvent(type=LLMStreamEventType.TEXT_START)
                yield LLMStreamEvent(type=LLMStreamEventType.TEXT_DELTA, content=delta.content)

            # Accumulate tool call deltas
            if hasattr(delta, "tool_calls") and delta.tool_calls:
                tc_delta = delta.tool_calls[0]
                if tc_delta.id:
                    tool_call_id = tc_delta.id

                if tc_delta.function:
                    if tc_delta.function.name:
                        tool_call_name = tc_delta.function.name
                    if tc_delta.function.arguments:
                        tool_call_args += tc_delta.function.arguments

        if think_started:
            yield LLMStreamEvent(type=LLMStreamEventType.THINK_END)

        # Yield accumulated tool call as final response (mutually exclusive with TEXT_END)
        if tool_call_id and tool_call_name:
            try:
                tool_params = json.loads(tool_call_args)
            except json.JSONDecodeError:
                tool_params = {}

            yield LLMStreamEvent(
                type=LLMStreamEventType.TOOL_CALL,
                tool_call=ToolCall(id=tool_call_id, name=tool_call_name, params=tool_params),
                usage=usage,
            )
        elif text_started:
            yield LLMStreamEvent(type=LLMStreamEventType.TEXT_END, usage=usage)

    def get_metadata(self) -> Metadata:
        context_window = self.MODELS.get(self._model, 128000)
        return Metadata(name=self._model, context_window=context_window, owned_by="openai")

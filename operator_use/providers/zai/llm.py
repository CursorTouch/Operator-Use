import os
import json
import logging
from typing import Iterator, AsyncIterator, List, Optional, overload
from pydantic import BaseModel
import httpx
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


class ChatZAI(BaseChatLLM):
    """
    Z.AI LLM implementation following the BaseChatLLM protocol.

    Z.AI (Zhipu AI) provides the GLM series of models including:
    - GLM-5 and GLM-5-Turbo (flagship models)
    - GLM-4.7 and GLM-4.7-Flash
    - GLM-4.6 (multimodal)
    - GLM-4.5 (multimodal)
    - GLM-4-32B
    """

    # Available models with context windows (tokens)
    # Source: https://docs.z.ai/api-reference/llm/chat-completion
    MODELS = {
        # GLM-5 series (latest)
        "glm-5": 131072,  # GLM-5 (flagship)
        "glm-5-turbo": 131072,  # GLM-5-Turbo (faster)
        # GLM-4.7 series
        "glm-4.7-b": 131072,  # GLM-4.7-B
        "glm-4.7-flash": 131072,  # GLM-4.7-Flash
        # GLM-4.6 series
        "glm-4.6-vision": 131072,  # GLM-4.6-Vision
        "glm-4.6b": 131072,  # GLM-4.6B
        # GLM-4.5 series
        "glm-4.5-vision": 131072,  # GLM-4.5-Vision
        "glm-4.5": 131072,  # GLM-4.5
        # GLM-4 series
        "glm-4-32b": 131072,  # GLM-4-32B
    }

    API_BASE = "https://api.z.ai/api/paas/v4"

    def __init__(
        self,
        model: str = "glm-5",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 60.0,
        temperature: Optional[float] = None,
        **kwargs,
    ):
        """
        Initialize the Z.AI LLM.

        Args:
            model (str): The model name to use.
            api_key (str, optional): Z.AI API key. Defaults to ZAI_API_KEY environment variable.
            base_url (str, optional): Base URL for the API.
            timeout (float): Request timeout.
            temperature (float, optional): Sampling temperature.
            **kwargs: Additional arguments for chat completions.
        """
        self._model = model
        self.api_key = api_key or os.environ.get("ZAI_API_KEY")
        self.base_url = base_url or self.API_BASE
        self.temperature = temperature
        self.timeout = timeout
        self.kwargs = kwargs

        if not self.api_key:
            logger.warning("ZAI_API_KEY environment variable not set")

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "zai"

    def _convert_messages(self, messages: List[BaseMessage]) -> List[dict]:
        """
        Convert BaseMessage objects to Z.AI-compatible message dictionaries.
        """
        zai_messages = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                zai_messages.append({"role": "system", "content": msg.content})
            elif isinstance(msg, HumanMessage):
                zai_messages.append({"role": "user", "content": msg.content})
            elif isinstance(msg, ImageMessage):
                # Z.AI supports multimodal input similar to OpenAI
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
                zai_messages.append({"role": "user", "content": content_list})
            elif isinstance(msg, AIMessage):
                msg_dict: dict = {"role": "assistant", "content": msg.content or ""}
                if getattr(msg, "thinking", None):
                    msg_dict["reasoning"] = msg.thinking
                zai_messages.append(msg_dict)
            elif isinstance(msg, ToolMessage):
                # Reconstruct for history consistency
                tool_call = {
                    "id": msg.id,
                    "type": "function",
                    "function": {"name": msg.name, "arguments": json.dumps(msg.params)},
                }
                zai_messages.append(
                    {"role": "assistant", "content": None, "tool_calls": [tool_call]}
                )
                zai_messages.append(
                    {"role": "tool", "tool_call_id": msg.id, "content": msg.content or ""}
                )
        return zai_messages

    def _convert_tools(self, tools: List[Tool]) -> List[dict]:
        """
        Convert Tool objects to Z.AI-compatible tool definitions.
        """
        return [{"type": "function", "function": tool.json_schema} for tool in tools]

    def _process_response(self, response: dict) -> LLMEvent:
        """Process Z.AI API response into AIMessage or ToolMessage."""
        choice = response["choices"][0]
        message = choice["message"]
        usage_data = response.get("usage", {})

        usage = TokenUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
            thinking_tokens=None,
        )

        thinking = message.get("reasoning") or message.get("reasoning_content")
        thinking_obj = Thinking(content=thinking, signature=None) if thinking else None

        if "tool_calls" in message and message["tool_calls"]:
            tool_call = message["tool_calls"][0]
            try:
                params = json.loads(tool_call["function"]["arguments"])
            except (json.JSONDecodeError, KeyError, TypeError):
                logger.warning(
                    f"Failed to parse tool arguments: {tool_call.get('function', {}).get('arguments', '')}"
                )
                params = {}
            return LLMEvent(
                type=LLMEventType.TOOL_CALL,
                tool_call=ToolCall(
                    id=tool_call.get("id", ""), name=tool_call["function"]["name"], params=params
                ),
                usage=usage,
            )
        return LLMEvent(
            type=LLMEventType.TEXT,
            content=message.get("content", ""),
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
        zai_messages = self._convert_messages(messages)
        zai_tools = self._convert_tools(tools) if tools else None

        params = {"model": self._model, "messages": zai_messages, **self.kwargs}

        if zai_tools:
            params["tools"] = zai_tools

        if self.temperature is not None:
            params["temperature"] = self.temperature

        if json_mode:
            params["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/chat/completions", json=params, headers=headers
            )
            response.raise_for_status()
            response_data = response.json()

        if structured_output:
            try:
                content_text = response_data["choices"][0]["message"]["content"]
                if content_text:
                    parsed = structured_output.model_validate_json(content_text)
                else:
                    parsed = structured_output()

                content = parsed.model_dump() if hasattr(parsed, "model_dump") else str(parsed)
                return LLMEvent(
                    type=LLMEventType.TEXT,
                    content=json.dumps(content) if isinstance(content, dict) else content,
                    usage=TokenUsage(
                        prompt_tokens=response_data.get("usage", {}).get("prompt_tokens", 0),
                        completion_tokens=response_data.get("usage", {}).get(
                            "completion_tokens", 0
                        ),
                        total_tokens=response_data.get("usage", {}).get("total_tokens", 0),
                    ),
                )
            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"Failed to parse structured output: {e}")

        return self._process_response(response_data)

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
        zai_messages = self._convert_messages(messages)
        zai_tools = self._convert_tools(tools) if tools else None

        params = {"model": self._model, "messages": zai_messages, **self.kwargs}

        if zai_tools:
            params["tools"] = zai_tools

        if self.temperature is not None:
            params["temperature"] = self.temperature

        if json_mode:
            params["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions", json=params, headers=headers
            )
            response.raise_for_status()
            response_data = response.json()

        if structured_output:
            try:
                content_text = response_data["choices"][0]["message"]["content"]
                if content_text:
                    parsed = structured_output.model_validate_json(content_text)
                else:
                    parsed = structured_output()

                content = parsed.model_dump() if hasattr(parsed, "model_dump") else str(parsed)
                return LLMEvent(
                    type=LLMEventType.TEXT,
                    content=json.dumps(content) if isinstance(content, dict) else content,
                    usage=TokenUsage(
                        prompt_tokens=response_data.get("usage", {}).get("prompt_tokens", 0),
                        completion_tokens=response_data.get("usage", {}).get(
                            "completion_tokens", 0
                        ),
                        total_tokens=response_data.get("usage", {}).get("total_tokens", 0),
                    ),
                )
            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"Failed to parse structured output: {e}")

        return self._process_response(response_data)

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
        zai_messages = self._convert_messages(messages)
        zai_tools = self._convert_tools(tools) if tools else None

        params = {"model": self._model, "messages": zai_messages, "stream": True, **self.kwargs}

        if zai_tools:
            params["tools"] = zai_tools

        if self.temperature is not None:
            params["temperature"] = self.temperature

        if json_mode:
            params["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=self.timeout) as client:
            with client.stream(
                "POST", f"{self.base_url}/chat/completions", json=params, headers=headers
            ) as response:
                response.raise_for_status()

                # Accumulators for streamed tool calls
                tool_call_id = None
                tool_call_name = None
                tool_call_args = ""
                usage = None

                text_started = False
                think_started = False

                for line in response.iter_lines():
                    if not line or line.startswith(":"):
                        continue

                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break

                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue

                        if not chunk.get("choices"):
                            if chunk.get("usage"):
                                usage = TokenUsage(
                                    prompt_tokens=chunk["usage"].get("prompt_tokens", 0),
                                    completion_tokens=chunk["usage"].get("completion_tokens", 0),
                                    total_tokens=chunk["usage"].get("total_tokens", 0),
                                )
                            continue

                        delta = chunk["choices"][0].get("delta", {})

                        reasoning_delta = delta.get("reasoning") or delta.get("reasoning_content")
                        if reasoning_delta:
                            if not think_started:
                                think_started = True
                                yield LLMStreamEvent(type=LLMStreamEventType.THINK_START)
                            yield LLMStreamEvent(
                                type=LLMStreamEventType.THINK_DELTA, content=reasoning_delta
                            )

                        if delta.get("content"):
                            if think_started:
                                yield LLMStreamEvent(type=LLMStreamEventType.THINK_END)
                                think_started = False
                            if not text_started:
                                text_started = True
                                yield LLMStreamEvent(type=LLMStreamEventType.TEXT_START)
                            yield LLMStreamEvent(
                                type=LLMStreamEventType.TEXT_DELTA, content=delta["content"]
                            )

                        # Accumulate tool call deltas
                        if delta.get("tool_calls"):
                            tc_delta = delta["tool_calls"][0]
                            if tc_delta.get("id"):
                                tool_call_id = tc_delta["id"]
                            if tc_delta.get("function"):
                                func = tc_delta["function"]
                                if func.get("name"):
                                    tool_call_name = func["name"]
                                if func.get("arguments"):
                                    tool_call_args += func["arguments"]

                # Yield accumulated tool call as final response
                if tool_call_id and tool_call_name:
                    try:
                        params = json.loads(tool_call_args)
                    except json.JSONDecodeError:
                        params = {}

                    yield LLMStreamEvent(
                        type=LLMStreamEventType.TOOL_CALL,
                        tool_call=ToolCall(id=tool_call_id, name=tool_call_name, params=params),
                        usage=usage,
                    )
                else:
                    if think_started:
                        yield LLMStreamEvent(type=LLMStreamEventType.THINK_END)
                    if text_started:
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
        zai_messages = self._convert_messages(messages)
        zai_tools = self._convert_tools(tools) if tools else None

        params = {"model": self._model, "messages": zai_messages, "stream": True, **self.kwargs}

        if zai_tools:
            params["tools"] = zai_tools

        if self.temperature is not None:
            params["temperature"] = self.temperature

        if json_mode:
            params["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST", f"{self.base_url}/chat/completions", json=params, headers=headers
            ) as response:
                response.raise_for_status()

                # Accumulators for streamed tool calls
                tool_call_id = None
                tool_call_name = None
                tool_call_args = ""
                usage = None

                text_started = False
                think_started = False

                async for line in response.aiter_lines():
                    if not line or line.startswith(":"):
                        continue

                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break

                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue

                        if not chunk.get("choices"):
                            if chunk.get("usage"):
                                usage = TokenUsage(
                                    prompt_tokens=chunk["usage"].get("prompt_tokens", 0),
                                    completion_tokens=chunk["usage"].get("completion_tokens", 0),
                                    total_tokens=chunk["usage"].get("total_tokens", 0),
                                )
                            continue

                        delta = chunk["choices"][0].get("delta", {})

                        reasoning_delta = delta.get("reasoning") or delta.get("reasoning_content")
                        if reasoning_delta:
                            if not think_started:
                                think_started = True
                                yield LLMStreamEvent(type=LLMStreamEventType.THINK_START)
                            yield LLMStreamEvent(
                                type=LLMStreamEventType.THINK_DELTA, content=reasoning_delta
                            )

                        if delta.get("content"):
                            if think_started:
                                yield LLMStreamEvent(type=LLMStreamEventType.THINK_END)
                                think_started = False
                            if not text_started:
                                text_started = True
                                yield LLMStreamEvent(type=LLMStreamEventType.TEXT_START)
                            yield LLMStreamEvent(
                                type=LLMStreamEventType.TEXT_DELTA, content=delta["content"]
                            )

                        # Accumulate tool call deltas
                        if delta.get("tool_calls"):
                            tc_delta = delta["tool_calls"][0]
                            if tc_delta.get("id"):
                                tool_call_id = tc_delta["id"]
                            if tc_delta.get("function"):
                                func = tc_delta["function"]
                                if func.get("name"):
                                    tool_call_name = func["name"]
                                if func.get("arguments"):
                                    tool_call_args += func["arguments"]

                # Yield accumulated tool call as final response
                if tool_call_id and tool_call_name:
                    try:
                        params = json.loads(tool_call_args)
                    except json.JSONDecodeError:
                        params = {}

                    yield LLMStreamEvent(
                        type=LLMStreamEventType.TOOL_CALL,
                        tool_call=ToolCall(id=tool_call_id, name=tool_call_name, params=params),
                        usage=usage,
                    )
                else:
                    if think_started:
                        yield LLMStreamEvent(type=LLMStreamEventType.THINK_END)
                    if text_started:
                        yield LLMStreamEvent(type=LLMStreamEventType.TEXT_END, usage=usage)

    def get_metadata(self) -> Metadata:
        context_window = self.MODELS.get(self._model, 131072)
        return Metadata(name=self._model, context_window=context_window, owned_by="zai")

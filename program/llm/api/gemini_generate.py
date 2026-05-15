from __future__ import annotations
import json
from collections.abc import AsyncIterator
from typing import Any
from google import genai
from google.genai import types as genai_types
from program.llm.api.base import BaseAPI
from program.llm.model.types import Model
from program.llm.types import (
    LLMContext, LLMEvent, Options, StopReason, ThinkingBudgets,
    StartEvent, EndEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent,
    ThinkingStartEvent, ThinkingDeltaEvent, ThinkingEndEvent,
    ToolCallStartEvent, ToolCallDeltaEvent, ToolCallEndEvent,
)
from program.message.types import (
    BaseMessage, SystemMessage, UserMessage, AssistantMessage, ToolMessage,
    TextContent, ImageContent, ThinkingContent, ToolCallContent, ToolResultContent,
)
from typing import Optional, TYPE_CHECKING
if TYPE_CHECKING:
    from program.tool.types import Tool

_STOP_REASON: dict[str, StopReason] = {
    "STOP": StopReason.Stop,
    "MAX_TOKENS": StopReason.Length,
    "SAFETY": StopReason.ContentFilter,
    "RECITATION": StopReason.ContentFilter,
}


def _messages_to_gemini(
    messages: list[BaseMessage],
) -> tuple[str | None, list[genai_types.Content]]:
    system: str | None = None
    contents: list[genai_types.Content] = []

    for msg in messages:
        if isinstance(msg, SystemMessage):
            system = "\n".join(c.content for c in msg.contents if isinstance(c, TextContent))
        elif isinstance(msg, UserMessage):
            parts: list[genai_types.Part] = []
            for item in msg.contents:
                if isinstance(item, TextContent):
                    parts.append(genai_types.Part(text=item.content))
                elif isinstance(item, ImageContent):
                    for b64, mime in item.to_base64():
                        parts.append(genai_types.Part(
                            inline_data=genai_types.Blob(mime_type=mime or "image/png", data=b64),
                        ))
            if parts:
                contents.append(genai_types.Content(role="user", parts=parts))
        elif isinstance(msg, AssistantMessage):
            parts = []
            for item in msg.contents:
                if isinstance(item, TextContent):
                    parts.append(genai_types.Part(text=item.content))
                elif isinstance(item, ToolCallContent):
                    parts.append(genai_types.Part(
                        function_call=genai_types.FunctionCall(
                            name=item.name,
                            args=item.args,
                        ),
                    ))
            if parts:
                contents.append(genai_types.Content(role="model", parts=parts))
        elif isinstance(msg, ToolMessage):
            parts = []
            for content in msg.contents:
                if isinstance(content, ToolResultContent):
                    parts.append(genai_types.Part(
                        function_response=genai_types.FunctionResponse(
                            name=content.id,
                            response={"result": content.content},
                        ),
                    ))
            if parts:
                contents.append(genai_types.Content(role="user", parts=parts))

    return system, contents


class GeminiGenerateAPI(BaseAPI):
    def __init__(self, options: Options) -> None:
        super().__init__(options)
        self._client = genai.Client(api_key=options.api_key)

    def _build_config(self, tools: Optional[list[Tool]] = None) -> genai_types.GenerateContentConfig:
        params: dict[str, Any] = {
            "temperature": self.options.temperature,
        }
        if self.options.max_tokens is not None:
            params["max_output_tokens"] = self.options.max_tokens

        budget = None
        if self.options.thinking_level is not None:
            budgets = self.options.thinking_budgets or ThinkingBudgets()
            budget = budgets.get(self.options.thinking_level)
        if budget is not None:
            params["thinking_config"] = genai_types.ThinkingConfig(
                thinking_budget=budget,
                include_thoughts=True,
            )
        
        if tools:
            params["tools"] = [
                genai_types.Tool(
                    function_declarations=[
                        genai_types.FunctionDeclaration(
                            name=t.name,
                            description=t.description,
                            parameters=t.schema.model_json_schema(),
                        )
                        for t in tools
                    ]
                )
            ]

        return genai_types.GenerateContentConfig(**params)

    async def stream(self, context: LLMContext, model: Model) -> AsyncIterator[LLMEvent]:  # type: ignore[override]
        system, contents = _messages_to_gemini(context.messages)
        config = self._build_config(tools=context.tools or None)
        if system:
            config.system_instruction = system

        if self.options.on_payload:
            payload = {"config": config, "contents": contents}
            modified = self.options.on_payload(payload)
            if modified is not None:
                config = modified.get("config", config)
                contents = modified.get("contents", contents)

        text_index = 0
        thinking_index = 0
        tool_index = 0
        text_started = False
        thinking_started = False
        text_buf = ""
        thinking_buf = ""

        yield StartEvent()

        try:
            async for chunk in await self._client.aio.models.generate_content_stream(
                model=model.id,
                contents=contents,
                config=config,
            ):
                if self._cancelled():
                    yield ErrorEvent(reason=StopReason.Abort, error="Cancelled")
                    return
                if not chunk.candidates:
                    continue

                candidate = chunk.candidates[0]
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if getattr(part, "thought", False) and part.text:
                            if not thinking_started:
                                yield ThinkingStartEvent(thinking=None)
                                thinking_started = True
                            thinking_buf += part.text
                            yield ThinkingDeltaEvent(thinking=ThinkingContent(content=part.text))
                        elif part.text:
                            if thinking_started:
                                yield ThinkingEndEvent(thinking=ThinkingContent(content=thinking_buf))
                                thinking_started = False
                                thinking_index += 1
                                thinking_buf = ""
                            if not text_started:
                                yield TextStartEvent(text=TextContent(content=""))
                                text_started = True
                            text_buf += part.text
                            yield TextDeltaEvent(text=TextContent(content=part.text))
                        elif part.function_call:
                            fc = part.function_call
                            tool_id = fc.name
                            args_str = json.dumps(dict(fc.args)) if fc.args else ""
                            yield ToolCallStartEvent(tool_call=ToolCallContent(id=tool_id, name=fc.name))
                            yield ToolCallDeltaEvent(tool_call=ToolCallContent(id=tool_id))
                            yield ToolCallEndEvent(tool_call=ToolCallContent(id=tool_id, name=fc.name, args=json.loads(args_str) if args_str else {}))
                            tool_index += 1

                finish_reason = getattr(candidate, "finish_reason", None)
                if finish_reason and str(finish_reason) not in ("", "FINISH_REASON_UNSPECIFIED"):
                    if thinking_started:
                        yield ThinkingEndEvent(thinking=ThinkingContent(content=thinking_buf))
                    if text_started:
                        yield TextEndEvent(text=TextContent(content=text_buf))
                    reason_str = finish_reason.name if hasattr(finish_reason, "name") else str(finish_reason)
                    yield EndEvent(reason=_STOP_REASON.get(reason_str, StopReason.Stop))
                    return

        except Exception as exc:
            yield ErrorEvent(reason=StopReason.Abort, error=str(exc))
            return

        if thinking_started:
            yield ThinkingEndEvent(thinking=ThinkingContent(content=thinking_buf))
        if text_started:
            yield TextEndEvent(text=TextContent(content=text_buf))
        yield EndEvent(reason=StopReason.Stop)

    async def invoke(self, context: LLMContext, model: Model) -> list[LLMEvent]:
        events: list[LLMEvent] = []
        async for event in self.stream(context, model=model):
            events.append(event)
        return events

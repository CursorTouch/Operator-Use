from __future__ import annotations

import pytest
from pydantic import BaseModel

from operator_use.inference.api.text.openai_completions import OpenAICompletionsAPI
from operator_use.inference.api.text.openai_responses import OpenAIResponsesAPI
from operator_use.inference.api.text.service import LLM
from operator_use.inference.model.types import Model
from operator_use.inference.types import LLMContext, LLMOptions, normalize_structured_response_format
from operator_use.message.types import UserMessage


class Answer(BaseModel):
    answer: str
    confidence: float


class _AuthStore:
    async def get_api_key(self, provider_id):
        return None


class _API:
    def __init__(self):
        self.options = LLMOptions()
        self.seen_contexts = []

    async def stream(self, context, model):
        self.seen_contexts.append(context)
        return
        yield

    async def invoke(self, context, model):
        self.seen_contexts.append(context)
        return []


def test_normalize_pydantic_response_format():
    formatted = normalize_structured_response_format(Answer)

    assert formatted is not None
    assert formatted.name == "Answer"
    assert formatted.schema["properties"]["answer"]["type"] == "string"
    assert formatted.strict is True

@pytest.mark.asyncio
async def test_llm_wrapper_preserves_context_response_format_for_stream_and_invoke():
    api = _API()
    llm = LLM.__new__(LLM)
    llm.api = api
    llm.model = Model(id="test", name="Test", provider="test")
    llm.provider_id = "test"
    llm._auth_store = _AuthStore()

    context = LLMContext(messages=[UserMessage.text("extract")], response_format=Answer)

    async for _ in llm.stream(context):
        pass
    await llm.invoke(context)

    assert api.seen_contexts[0].response_format is Answer
    assert api.seen_contexts[1].response_format is Answer


@pytest.mark.asyncio
async def test_openai_responses_payload_includes_structured_output():
    captured = {}

    def capture(payload):
        captured.update(payload)
        raise RuntimeError("stop")

    api = OpenAIResponsesAPI(LLMOptions(api_key="key", on_payload=capture))
    context = LLMContext(messages=[UserMessage.text("extract")], response_format=Answer)

    with pytest.raises(RuntimeError):
        await anext(api.stream(context, Model(id="gpt-test", name="GPT", provider="openai")))

    assert captured["text"]["format"]["type"] == "json_schema"
    assert captured["text"]["format"]["name"] == "Answer"


@pytest.mark.asyncio
async def test_openai_chat_payload_includes_structured_output():
    captured = {}

    def capture(payload):
        captured.update(payload)
        raise RuntimeError("stop")

    api = OpenAICompletionsAPI(LLMOptions(api_key="key", on_payload=capture))
    context = LLMContext(messages=[UserMessage.text("extract")], response_format=Answer)

    with pytest.raises(RuntimeError):
        await anext(api.stream(context, Model(id="gpt-test", name="GPT", provider="openai")))

    assert captured["response_format"]["type"] == "json_schema"
    assert captured["response_format"]["json_schema"]["name"] == "Answer"

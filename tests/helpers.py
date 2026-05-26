"""Shared helpers reused across all battle test files."""
from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from pydantic import BaseModel

from program.compaction.strategy.summarization.service import SummarizationCompaction as Compaction
from program.compaction.strategy.types import CompactionSettings, CompactionPreparation
from program.agent.types import AgentContext
from program.engine.service import Engine
from program.engine.types import AgentEvent, Options
from program.hooks.service import Hooks
from program.inference.types import (
    LLMContext, LLMEvent, StopReason,
    StartEvent, EndEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent,
    ToolCallStartEvent, ToolCallEndEvent,
)
from program.message.types import (
    TextContent, ToolCallContent, ToolResultContent,
    UserMessage, AssistantMessage, ToolMessage, Role, Usage,
)
from program.session.manager import SessionManager
from program.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

# Forward-ref fix for CompactionPreparation
from program.message.types import AgentMessage as _AgentMessage
CompactionPreparation.model_rebuild(_types_namespace={"AgentMessage": _AgentMessage})


# ---------------------------------------------------------------------------
# Schema stubs
# ---------------------------------------------------------------------------

class AnyParams(BaseModel):
    pass


# ---------------------------------------------------------------------------
# FakeLLM — replays pre-defined event sequences
# ---------------------------------------------------------------------------

class FakeLLM:
    """Replays pre-defined event sequences; call_count tracks total LLM calls."""
    def __init__(self, *sequences: list[LLMEvent]):
        self._seqs = list(sequences)
        self._idx = 0
        self.call_count = 0
        self.contexts: list[LLMContext] = []
        self.model = SimpleNamespace(name="fake", provider="fake")
        self.api = SimpleNamespace(options=SimpleNamespace())

    async def stream(self, context: LLMContext) -> AsyncIterator[LLMEvent]:
        self.contexts.append(LLMContext(
            messages=list(context.messages),
            tools=list(context.tools),
            response_format=context.response_format,
        ))
        events = self._seqs[min(self._idx, len(self._seqs) - 1)]
        self._idx += 1
        self.call_count += 1
        for e in events:
            yield e

    async def invoke(self, context: LLMContext, thinking_level=None) -> list[LLMEvent]:
        self.contexts.append(LLMContext(
            messages=list(context.messages),
            tools=list(context.tools),
            response_format=context.response_format,
        ))
        events = self._seqs[min(self._idx, len(self._seqs) - 1)]
        self._idx += 1
        self.call_count += 1
        return events


# ---------------------------------------------------------------------------
# Event sequence builders
# ---------------------------------------------------------------------------

def text_seq(text: str = "ok", input_tokens: int = 100, output_tokens: int = 50) -> list[LLMEvent]:
    return [
        StartEvent(),
        TextStartEvent(text=TextContent(content="")),
        TextDeltaEvent(text=TextContent(content=text)),
        TextEndEvent(text=TextContent(content=text)),
        EndEvent(reason=StopReason.Stop, input_tokens=input_tokens, output_tokens=output_tokens),
    ]


def tool_call_seq(tool_id: str, name: str, args: dict | None = None) -> list[LLMEvent]:
    tc = ToolCallContent(id=tool_id, name=name, args=args or {})
    return [
        StartEvent(),
        ToolCallStartEvent(tool_call=ToolCallContent(id=tool_id, name=name)),
        ToolCallEndEvent(tool_call=tc),
        EndEvent(reason=StopReason.ToolCalls),
    ]


def error_seq(error: str = "boom") -> list[LLMEvent]:
    return [StartEvent(), ErrorEvent(reason=StopReason.Error, error=error)]


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------

def make_tool(
    name: str,
    result: str = "ok",
    is_error: bool = False,
    terminate: bool = False,
    execution_mode: ToolExecutionMode = ToolExecutionMode.Sequential,
    delay: float = 0.0,
) -> Tool:
    class _T(Tool):
        async def execute(self, invocation, tool_execution_update_callback=None, signal=None, **kwargs):
            if delay:
                await asyncio.sleep(delay)
            return ToolResult(id=invocation.id, content=result, is_error=is_error, metadata={}, terminate=terminate)
    return _T(name=name, description="battle tool", schema=AnyParams, kind=ToolKind.Read, execution_mode=execution_mode)


# ---------------------------------------------------------------------------
# Engine event collector
# ---------------------------------------------------------------------------

async def collect_events(engine: Engine, messages=None) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    await engine.subscribe(lambda e: events.append(e))
    await engine.run(AgentContext(
        system_prompt=engine.system_prompt or "",
        messages=messages or [UserMessage.text("go")],
        tools=engine.tools,
    ))
    return events


# ---------------------------------------------------------------------------
# Minimal Agent factory (for tests that need the full Agent stack)
# ---------------------------------------------------------------------------

def make_agent(llm, tools=None, hooks=None, compaction_settings=None):
    """Return (agent, session_manager) with minimal dependencies."""
    from program.agent.service import Agent
    from program.agent.types import AgentConfig
    from program.extension.runtime import ExtensionRuntime
    from program.extension.types import ExtensionContext, LoadExtensionsResult
    from program.resource.types import BaseResourceLoader

    class _FakeLoader(BaseResourceLoader):
        def get_extensions(self): return LoadExtensionsResult()
        def get_skills(self): return [], []
        def get_tools(self): return []
        def get_commands(self): return []
        def get_hooks(self): return []
        def get_context_files(self): return []
        def get_system_prompt(self): return None
        def get_append_system_prompt(self): return []
        def get_subagent_profiles(self): return []
        def extend_resources(self, paths): pass
        def get_diagnostics(self, runtime=None): return []
        async def reload(self): pass

    h = hooks or Hooks()
    sm = SessionManager.in_memory()
    engine = Engine(llm=llm, tools=tools or [], hooks=h)
    load_result = LoadExtensionsResult()
    cs = compaction_settings or CompactionSettings(enabled=False)
    config = AgentConfig(cwd=Path("/tmp"), retry_enabled=False, retry_max_retries=0, retry_base_delay_ms=0)
    agent = Agent(
        engine=engine,
        session_manager=sm,
        resource_loader=_FakeLoader(),
        extension_runtime=ExtensionRuntime(load_result, cast(ExtensionContext, None), h),
        compaction=Compaction(llm=llm, settings=cs),
        config=config,
    )
    agent._extensions = ExtensionRuntime(load_result, agent, h)
    return agent, sm


# ---------------------------------------------------------------------------
# Mistral live helper — requires MISTRAL_API_KEY in .env
# ---------------------------------------------------------------------------

def make_mistral_llm(model_id: str = "mistral-small-latest"):
    """Create a real MistralChatAPI instance for live tests."""
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.environ.get("MISTRAL_API_KEY", "")
    from program.inference.api.text.mistral_chat import MistralChatAPI
    from program.inference.types import LLMOptions

    from program.inference.model.types import Model

    class _Wrapper:
        def __init__(self):
            self._api = MistralChatAPI(options=LLMOptions(api_key=api_key))
            self._model = Model(id=model_id, name=model_id, provider="mistral")

        async def stream(self, context: LLMContext) -> AsyncIterator[LLMEvent]:
            async for ev in self._api.stream(context, self._model):
                yield ev

        async def invoke(self, context: LLMContext, thinking_level=None):
            return [ev async for ev in self._api.stream(context, self._model)]

    return _Wrapper()

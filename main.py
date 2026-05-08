import asyncio
import os
from program.llm.service import LLM
from program.llm.types import (
    Options, StartEvent, EndEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent,
    ThinkingStartEvent, ThinkingDeltaEvent, ThinkingEndEvent,
    ToolCallStartEvent, ToolCallDeltaEvent, ToolCallEndEvent,
)
from program.message.types import UserMessage, TextContent
from dotenv import load_dotenv
load_dotenv()

async def main():
    api_key = os.environ.get("NVIDIA_API_KEY", "")
    if not api_key:
        api_key = input("Enter your NVIDIA API key: ").strip()

    model_id = "nvidia/llama-3.3-nemotron-super-49b-v1"
    llm = LLM(
        model_id=model_id,
        provider="nvidia",
        options=Options(api_key=api_key),
    )

    messages = [UserMessage(contents=[TextContent(content="Say hello in one sentence.")])]

    print(f"Streaming response from {model_id}:\n")
    async for event in llm.stream(messages):
        if isinstance(event, StartEvent):
            print("[StartEvent]")
        elif isinstance(event, TextStartEvent):
            print("[TextStartEvent]")
        elif isinstance(event, TextDeltaEvent):
            print(event.data.text.content, end="", flush=True)
        elif isinstance(event, TextEndEvent):
            print(f"\n[TextEndEvent]")
        elif isinstance(event, ThinkingStartEvent):
            print(f"[ThinkingStartEvent]")
        elif isinstance(event, ThinkingDeltaEvent):
            thinking = event.data.thinking.content if event.data.thinking else ""
            print(f"[ThinkingDelta] {thinking}", end="", flush=True)
        elif isinstance(event, ThinkingEndEvent):
            thinking = event.data.thinking.content if event.data.thinking else ""
            print(f"\n[ThinkingEndEvent]")
        elif isinstance(event, ToolCallStartEvent):
            tc = event.data.tool_call
            print(f"[ToolCallStartEvent] id={tc.id if tc else ''}, name={tc.name if tc else ''}")
        elif isinstance(event, ToolCallDeltaEvent):
            print(f"[ToolCallDelta]")
        elif isinstance(event, ToolCallEndEvent):
            tc = event.data.tool_call
            print(f"[ToolCallEndEvent] id={tc.id if tc else ''}, name={tc.name if tc else ''}")
        elif isinstance(event, EndEvent):
            print(f"\n[EndEvent] reason={event.reason}")
        elif isinstance(event, ErrorEvent):
            print(f"\n[ErrorEvent] reason={event.reason}, message={event.message}")


if __name__ == "__main__":
    asyncio.run(main())

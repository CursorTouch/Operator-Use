import asyncio
import os
from program.llm.service import LLM
from program.llm.types import Options, DoneEvent, ErrorEvent, TextDeltaEvent
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

    print(f"Streaming response from {model_id}:")
    async for event in llm.stream(messages):
        if isinstance(event, TextDeltaEvent):
            print(event.data.text, end="", flush=True)
        elif isinstance(event, DoneEvent):
            print(f"\n\nDone ({event.reason})")
        elif isinstance(event, ErrorEvent):
            print(f"\nError: {event.message}")


if __name__ == "__main__":
    asyncio.run(main())

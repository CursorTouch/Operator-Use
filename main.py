import asyncio
import os
from program.llm.provider.builtins import API_PROVIDERS
from program.llm.types import Options, DoneEvent, ErrorEvent, TextDeltaEvent
from program.message.types import UserMessage, TextContent
from dotenv import load_dotenv
load_dotenv()

async def main():
    provider = next(p for p in API_PROVIDERS if p.name == "nvidia")

    api_key = os.environ.get("NVIDIA_API_KEY", "")
    if not api_key:
        api_key = input("Enter your NVIDIA API key: ").strip()

    options = Options(
        api_key=api_key,
        base_url=provider.options.base_url,
    )
    api = provider.api(options)

    messages = [UserMessage(contents=[TextContent(content="Say hello in one sentence.")])]

    model = "nvidia/llama-3.3-nemotron-super-49b-v1"
    print(f"Streaming response from {model}:")
    async for event in api.stream(messages, model=model):
        if isinstance(event, TextDeltaEvent):
            print(event.data.text, end="", flush=True)
        elif isinstance(event, DoneEvent):
            print(f"\n\nDone ({event.reason})")
        elif isinstance(event, ErrorEvent):
            print(f"\nError: {event.message}")


if __name__ == "__main__":
    asyncio.run(main())

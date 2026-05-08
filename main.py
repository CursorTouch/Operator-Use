import asyncio
import os
from program.llm.service import LLM
from program.llm.types import (
    Options, StartEvent, EndEvent, ErrorEvent,
    TextStartEvent, TextDeltaEvent, TextEndEvent,
    ThinkingStartEvent, ThinkingDeltaEvent, ThinkingEndEvent,
    ToolCallStartEvent, ToolCallDeltaEvent, ToolCallEndEvent,
)
from program.message.types import UserMessage, AssistantMessage, ToolMessage, TextContent, ThinkingContent, ToolCallContent, ToolResultContent
from program.llm.types import StopReason
from dotenv import load_dotenv
load_dotenv()

async def main():
    api_key = os.environ.get("NVIDIA_API_KEY", "")
    if not api_key:
        api_key = input("Enter your NVIDIA API key: ").strip()

    model_id = "nvidia/llama-3.3-nemotron-super-49b-v1"

    # Define a tool injection hook with multiple tools
    def inject_tools(params):
        params["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get the current weather in a given location",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string", "description": "The city and state, e.g. London, UK"},
                            "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
                        },
                        "required": ["location"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_time",
                    "description": "Get the current time in a given location",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string", "description": "The city and state, e.g. London, UK"}
                        },
                        "required": ["location"]
                    }
                }
            }
        ]
        params["tool_choice"] = "auto"
        return params

    llm = LLM(
        model_id = model_id,
        provider="nvidia",
        options=Options(
            api_key=api_key,
            on_payload=inject_tools
        ),
    )

    # Initial prompt
    messages = [UserMessage(contents=[TextContent(content="What is the weather in London and what time is it there?")])]

    while True:
        # 1. Construct the AssistantMessage at the place of usage
        assistant_msg = AssistantMessage()

        print(f"\n--- Starting Turn (LLM Prediction) ---")
        async for event in llm.stream(messages):
            match event:
                case StartEvent():
                    print("[StartEvent]")
                
                case TextStartEvent():
                    if not assistant_msg.contents or not isinstance(assistant_msg.contents[-1], TextContent):
                        assistant_msg.contents.append(TextContent(content=""))
                    print("[TextStartEvent]")
                    
                case TextDeltaEvent(text=text):
                    if not assistant_msg.contents or not isinstance(assistant_msg.contents[-1], TextContent):
                        assistant_msg.contents.append(TextContent(content=""))
                    assistant_msg.contents[-1].content += text.content
                    print(text.content, end="", flush=True)
                    
                case TextEndEvent():
                    print(f"\n[TextEndEvent]")
                    
                case ToolCallStartEvent(tool_call=tc):
                    print(f"[ToolCallStartEvent] id={tc.id if tc and tc.id else ''}, name={tc.name if tc and tc.name else ''}")
                    
                case ToolCallDeltaEvent():
                    print(".", end="", flush=True)
                    
                case ToolCallEndEvent(tool_call=tc):
                    if tc:
                        assistant_msg.contents.append(tc)
                    print(f"\n[ToolCallEndEvent] id={tc.id if tc else ''}, name={tc.name if tc else ''}, args={tc.args if tc else ''}")
                    
                case EndEvent(reason=reason):
                    assistant_msg.stop_reason = reason
                    print(f"\n[EndEvent] reason={reason}")
                    
                case ErrorEvent(reason=reason, error=err):
                    print(f"\n[ErrorEvent] reason={reason}, error={err}")

        # 2. Add the LLM's response to the conversation history
        messages.append(assistant_msg)

        # 3. Check if the LLM wants to call tools
        if assistant_msg.stop_reason == StopReason.ToolCalls:
            print("\n--- Executing Tool Calls ---")
            results = []
            for content in assistant_msg.contents:
                if isinstance(content, ToolCallContent):
                    # Mocking tool execution
                    if content.name == "get_weather":
                        output = f"Sunny, 20°C in {content.args.get('location', 'unknown')}"
                    elif content.name == "get_time":
                        import datetime
                        output = f"The time is {datetime.datetime.now().strftime('%H:%M')} in {content.args.get('location', 'unknown')}"
                    else:
                        output = "Tool not found."
                    
                    print(f"Executed {content.name}: {output}")
                    
                    # 4. Create ToolResultContent for each call
                    results.append(ToolResultContent(
                        id=content.id,
                        content=output
                    ))
            
            # 5. Return ALL results in a SINGLE ToolMessage
            messages.append(ToolMessage(contents=results))
            
            # Continue the loop for the next turn
            continue
        else:
            # If it's a normal stop (not a tool call), we are done!
            break

    print("\n--- Final Conversation History ---")
    for msg in messages:
        summary = ""
        for c in msg.contents:
            if isinstance(c, ToolResultContent):
                summary += f"[Result {c.id}: {c.content[:20]}...] "
            else:
                summary += repr(c)[:50] + "... "
        print(f"[{msg.role.upper()}]: {summary}")


if __name__ == "__main__":
    asyncio.run(main())

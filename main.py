import asyncio
import os
from pydantic import BaseModel, Field
from program.llm.service import LLM, Options as LLMOptions
from program.agent.service import Agent
from program.agent.types import Options as AgentOptions, AgentEvent, AgentEventType
from program.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult
from program.message.types import UserMessage, TextContent, AssistantMessage, ToolMessage
from dotenv import load_dotenv

load_dotenv()

class WeatherArgs(BaseModel):
    location: str = Field(description="The city and state, e.g. London, UK")
    unit: str = Field(default="celsius", description="The unit of temperature", pattern="^(celsius|fahrenheit)$")

class WeatherTool(Tool):
    def __init__(self):
        super().__init__(
            name="get_weather",
            description="Get the current weather in a given location",
            schema=WeatherArgs,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel
        )
    
    async def execute(self, invocation: ToolInvocation, **kwargs) -> ToolResult:
        location = invocation.params.get("location", "unknown")
        return ToolResult.ok(id=invocation.id, content=f"Sunny, 20°C in {location}")

class TimeArgs(BaseModel):
    location: str = Field(description="The city and state, e.g. London, UK")

class TimeTool(Tool):
    def __init__(self):
        super().__init__(
            name="get_time",
            description="Get the current time in a given location",
            schema=TimeArgs,
            kind=ToolKind.Read,
            execution_mode=ToolExecutionMode.Parallel
        )
    
    async def execute(self, invocation: ToolInvocation, **kwargs) -> ToolResult:
        import datetime
        location = invocation.params.get("location", "unknown")
        time_str = datetime.datetime.now().strftime('%H:%M')
        return ToolResult.ok(id=invocation.id, content=f"The time is {time_str} in {location}")

async def main():
    api_key = os.environ.get("MISTRAL_API_KEY", "")
    if not api_key:
        api_key = input("Enter your Mistral API key: ").strip()

    model_id = "mistral-large-latest"
    
    tools = [WeatherTool(), TimeTool()]

    llm = LLM(
        model_id=model_id,
        provider="mistral",
        options=LLMOptions(api_key=api_key),
    )

    # State for testing steering and follow-ups
    steering_delivered = False
    follow_up_delivered = False

    def get_steering():
        nonlocal steering_delivered
        if not steering_delivered:
            steering_delivered = True
            print("\n[HOOK] Injecting Steering Message!")
            return [UserMessage(contents=[TextContent(content="Actually, also tell me the capital of France.")])]
        return []

    def get_follow_up():
        nonlocal follow_up_delivered
        if not follow_up_delivered:
            follow_up_delivered = True
            print("\n[HOOK] Injecting Follow-up Task!")
            return [UserMessage(contents=[TextContent(content="Now tell me a joke.")])]
        return []

    def process_event(event: AgentEvent):
        match event.type:
            case AgentEventType.TurnStart:
                print("\n--- New Turn Start ---")
            case AgentEventType.MessageStart:
                print(f"\n[{event.message.role.upper()}] ", end="")
            case AgentEventType.MessageUpdate:
                pass
            case AgentEventType.MessageEnd:
                msg = event.message
                content_summary = ""
                for c in msg.contents:
                    if hasattr(c, 'content'):
                        content_summary += str(c.content)
                    elif hasattr(c, 'name'):
                        content_summary += f"[ToolCall: {c.name}] "
                
                if msg.role == "assistant" or msg.role == "user" or msg.role == "tool":
                    print(content_summary)
            case AgentEventType.ToolExecutionStart:
                print(f"\n[Tool] Executing {event.tool_call.name}...")
            case AgentEventType.ToolExecutionEnd:
                print(f"[Tool] Result: {event.tool_result.content}")
            case AgentEventType.AgentError:
                print(f"\n[ERROR] Agent Error: {event.error}")

    agent = Agent(
        llm=llm,
        tools=tools,
        options=AgentOptions(
            get_steering_messages=get_steering,
            get_follow_up_messages=get_follow_up
        )
    )

    # Override the default process_events with our printer
    agent.process_events = process_event

    print("--- Starting Agent Run ---")
    messages = [UserMessage(contents=[TextContent(content="What is the weather in London and what time is it there?")])]
    
    await agent.run(messages)

    print("\n--- Final Conversation History ---")
    for msg in agent.state.messages:
        content_summary = ""
        for c in msg.contents:
            if hasattr(c, 'content'):
                content_summary += str(c.content)
            elif hasattr(c, 'name'):
                content_summary += f"[ToolCall: {c.name}] "
        print(f"[{msg.role.upper()}]: {content_summary}")

if __name__ == "__main__":
    asyncio.run(main())

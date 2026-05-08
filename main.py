import asyncio
import os
import sys
from program.llm.service import LLM, Options as LLMOptions
from program.agent.service import Agent
from program.agent.types import (
    Options as AgentOptions, AgentEvent, AgentEventType,
    AgentStartEvent, AgentEndEvent,
    TurnStartEvent, MessageUpdateEvent, MessageEndEvent,
    ToolExecutionStartEvent, ToolExecutionEndEvent, AgentErrorEvent
)
from program.message.types import (
    UserMessage, TextContent, AssistantMessage, ToolMessage, SystemMessage
)
from dotenv import load_dotenv

load_dotenv()

SYSTEM_PROMPT = """You are a highly skilled AI coding agent. Your goal is to help the user with their programming tasks, including:
1. Exploring the codebase using `list_dir`, `read_file`, and `search_file`.
2. Making changes using `write_file` and `edit_file`.
3. Running commands, tests, and builds using `terminal`.
4. Researching documentation and libraries using `web_search` and `web_fetch`.

Follow these rules:
- Always explore before making changes.
- When editing, use `edit_file` for small, precise changes. Use `write_file` for new files or complete rewrites.
- Use `terminal` to verify your changes (e.g., run tests).
- Be concise and professional.
"""

from program.agent.types import (
    Options as AgentOptions, AgentEvent, AgentEventType,
    AgentStartEvent, AgentEndEvent,
    TurnStartEvent, MessageUpdateEvent, MessageEndEvent,
    ToolExecutionStartEvent, ToolExecutionEndEvent, AgentErrorEvent
)

# Global state to track labels during streaming
streaming_state = {
    "current_role": None,
    "label_printed": False
}

def process_event(event: AgentEvent):
    match event:
        case TurnStartEvent():
            streaming_state["current_role"] = None
            streaming_state["label_printed"] = False
        case MessageUpdateEvent(message=msg):
            if msg and msg.role == "assistant":
                for c in msg.contents:
                    if hasattr(c, 'type'):
                        if c.type == "thinking" and c.content:
                            if streaming_state["current_role"] != "thinking":
                                print(f"\n\033[1;30m[Thinking]: \033[0m", end="", flush=True)
                                streaming_state["current_role"] = "thinking"
                            sys.stdout.write(f"\033[1;30m{c.content}\033[0m")
                            sys.stdout.flush()
                        elif c.type == "text" and c.content:
                            if streaming_state["current_role"] != "assistant":
                                print(f"\n\033[1;34m[Assistant]: \033[0m", end="", flush=True)
                                streaming_state["current_role"] = "assistant"
                            sys.stdout.write(c.content)
                            sys.stdout.flush()
        case MessageEndEvent(message=msg):
            if msg and msg.role == "assistant":
                # Print a final newline after streaming ends
                print()
                streaming_state["current_role"] = None
        case ToolExecutionStartEvent(tool_call=tc):
            args_str = ", ".join(f"{k}={v}" for k, v in tc.args.items())
            print(f"\n\033[1;33m[ToolCall]: {tc.name}({args_str})\033[0m")
        case ToolExecutionEndEvent(tool_result=res):
            content = str(res.content)
            if len(content) > 500:
                content = content[:500] + " \033[1;30m... [Truncated]\033[0m"
            print(f"\033[1;32m[ToolResult]:\033[0m {content}")
        case AgentErrorEvent(error=err):
            print(f"\033[1;31m[Error]: {err}\033[0m")

async def main():
    api_key = os.environ.get("MISTRAL_API_KEY", "")
    if not api_key:
        api_key = input("Enter your Mistral API key: ").strip()
        if not api_key:
             print("Mistral API key is required.")
             return

    model_id = "mistral-large-latest"
    
    from program.agent.tools import (
        ListDirTool, ReadFileTool, WriteFileTool, EditFileTool,
        TerminalTool, WebFetchTool, WebSearchTool
    )
    
    tools = [
        ListDirTool(),
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        TerminalTool(),
        WebFetchTool(),
        WebSearchTool()
    ]

    llm = LLM(
        model_id=model_id,
        provider="mistral",
        options=LLMOptions(api_key=api_key),
    )

    agent = Agent(
        llm=llm,
        tools=tools,
        options=AgentOptions()
    )

    agent.process_events = process_event

    print("--- AI Coding Agent Started ---")
    print("Type 'exit' or 'quit' to stop.")

    messages = [SystemMessage(contents=[TextContent(content=SYSTEM_PROMPT)])]
    
    while True:
        try:
            user_input = input("\n\033[1;36m[User]:\033[0m ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                break
            
            messages.append(UserMessage(contents=[TextContent(content=user_input)]))
            
            # Run the agent with the message history
            await agent.run(messages)
            
            # The agent.run method appends Assistant and Tool messages to the list it receives.
            # So the 'messages' list is now updated with the agent's response and tool interactions.
            
        except KeyboardInterrupt:
            print("\nInterrupted by user.")
            break
        except Exception as e:
            print(f"\n[CRITICAL ERROR] {e}")

if __name__ == "__main__":
    asyncio.run(main())

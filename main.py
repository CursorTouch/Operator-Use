import asyncio
import os
import sys
from program.llm.service import LLM, Options as LLMOptions
from program.agent.service import Agent
from program.agent.types import (
    Options as AgentOptions, AgentEvent, AgentEventType,
    TurnStartEvent, MessageStartEvent, MessageEndEvent,
    ToolExecutionStartEvent, ToolExecutionEndEvent, AgentErrorEvent
)
from program.message.types import UserMessage, TextContent, AssistantMessage, ToolMessage,SystemMessage
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

def process_event(event: AgentEvent):
    match event:
        case TurnStartEvent():
            print("\n" + "="*20 + " NEW TURN " + "="*20)
        case MessageStartEvent(message=msg):
            if msg:
                role = msg.role.upper()
                if role != "TOOL":
                    print(f"\n[{role}] ", end="", flush=True)
        case MessageEndEvent(message=msg):
            if msg:
                for c in msg.contents:
                    if hasattr(c, 'type'):
                        if c.type == "text" and c.content:
                            print(c.content)
                        elif c.type == "thinking" and c.content:
                            print(f"\n[THOUGHTS]\n{c.content}\n" + "-"*20)
                    elif hasattr(c, 'name'):
                        # Tool calls are handled by ExecutionStart
                        pass
        case ToolExecutionStartEvent(tool_call=tc):
            print(f"\n[TOOL CALL] {tc.name}({tc.args})")
        case ToolExecutionEndEvent(tool_result=res):
            # Optionally print a snippet of the result if it's long
            content = str(res.content)
            if len(content) > 200:
                content = content[:200] + "..."
            print(f"[TOOL RESULT] {content}")
        case AgentErrorEvent(error=err):
            print(f"\n[ERROR] {err}")

async def main():
    api_key = os.environ.get("NVIDIA_API_KEY", "")
    if not api_key:
        api_key = input("Enter your NVIDIA API key: ").strip()
        if not api_key:
             print("NVIDIA API key is required.")
             return

    model_id = "nvidia/nemotron-3-super-120b-a12b"
    
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
        provider="nvidia",
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
            user_input = input("\nUser > ").strip()
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

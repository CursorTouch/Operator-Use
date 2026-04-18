"""Agent service: LLM loop + tool execution.

The Agent no longer owns the bus consume loop, STT/TTS, or message building.
Those responsibilities belong to the Orchestrator. The Agent receives an
already-built HumanMessage/ImageMessage via run() and returns an AIMessage.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Awaitable

from operator_use.messages import AIMessage, HumanMessage, ImageMessage, ToolMessage
from operator_use.agent.context import Context
from operator_use.agent.context.service import PromptMode
from operator_use.agent.tools import ToolRegistry, BUILTIN_TOOLS
from operator_use.bus import IncomingMessage
from operator_use.providers.events import LLMEvent, LLMEventType, LLMStreamEvent, LLMStreamEventType, Thinking
from operator_use.session import SessionStore, Session
from operator_use.subagent.manager import SubagentManager
from operator_use.process import ProcessManager
from operator_use.agent.hooks import Hooks, HookEvent
from operator_use.agent.hooks.events import (
    BeforeAgentStartContext,
    AfterAgentStartContext,
    BeforeAgentEndContext,
    AfterAgentEndContext,
    BeforeToolCallContext,
    AfterToolCallContext,
    BeforeLLMCallContext,
    AfterLLMCallContext,
)

if TYPE_CHECKING:
    from operator_use.providers.base import BaseChatLLM
    from operator_use.tracing import Tracer
    from operator_use.crons.service import Cron
    from operator_use.plugins import Plugin

logger = logging.getLogger(__name__)


class Agent:
    """Runs the LLM agentic loop for a single workspace/persona.

    Receives pre-built HumanMessage/ImageMessage from the Orchestrator via
    run() and returns an AIMessage. Has no knowledge of channels, STT, TTS,
    or bus consumption — those are Orchestrator concerns.
    """

    def __init__(
        self,
        llm: "BaseChatLLM",
        agent_id: str = "operator",
        description: str = "",
        workspace: Path | None = None,
        sessions: SessionStore | None = None,
        max_iterations: int = 100,
        userdata_dir: Path | None = None,
        cron: "Cron | None" = None,
        gateway=None,
        bus=None,
        tools: list | None = None,
        exclude_tools: list | None = None,
        prompt_mode: PromptMode = PromptMode.FULL,
        system_prompt: str = "",
        subagent_config=None,
        acp_registry: dict | None = None,
        plugins: "list[Plugin] | None" = None,
        image=None,
        search=None,
        mcp_servers: dict | None = None,
        tracer: "Tracer | None" = None,
    ):
        self.agent_id = agent_id
        self.description = description
        if workspace is None:
            from operator_use.config.paths import get_named_workspace_dir

            workspace = get_named_workspace_dir("operator")
        self.workspace = workspace
        self.sessions = sessions or SessionStore(workspace=self.workspace)
        self.context = Context(workspace=self.workspace, mcp_servers=mcp_servers)
        self.tool_register = ToolRegistry()
        self.max_iterations = max_iterations
        self.llm = llm
        self.cron = cron
        self.gateway = gateway
        self.bus = bus
        self.tracer = tracer
        self.subagent_manager = SubagentManager(
            llm=llm, bus=bus, config=subagent_config, tracer=tracer
        )
        self.process_store = ProcessManager()
        self.hooks = Hooks()

        # Register tracer hooks if provided
        if self.tracer:
            self.tracer.register(self.hooks)

        self.prompt_mode = prompt_mode
        self.system_prompt = system_prompt
        self.tool_register.register_tools(tools if tools is not None else BUILTIN_TOOLS)
        if exclude_tools:
            self.tool_register.unregister_tools(exclude_tools)

        self.tool_register.register_workspace_tools(self.workspace / "tools")

        self.context.skills.register_history_hook(self.hooks)
        self.context.interceptor.register_history_hook(self.hooks)

        # Set stable tool extensions (don't change per message)
        self.tool_register.set_extension("_workspace", self.workspace)
        self.tool_register.set_extension("_bus", self.bus)
        self.tool_register.set_extension("_gateway", self.gateway)
        self.tool_register.set_extension("_cron", self.cron)
        self.tool_register.set_extension("_subagent_manager", self.subagent_manager)
        self.tool_register.set_extension("_process_store", self.process_store)
        self.tool_register.set_extension("_acp_registry", acp_registry or {})
        self.tool_register.set_extension("_llm", self.llm)
        self.tool_register.set_extension("_image", image)
        self.tool_register.set_extension("_search", search)
        self.tool_register.set_extension("_agent", self)
        self.tool_register.set_extension("_agent_id", self.agent_id)
        self.tool_register.set_extension("_interceptor", self.context.interceptor)

        # Wire plugins
        self.plugins: "list[Plugin]" = plugins or []
        for plugin in self.plugins:
            plugin.register_tools(self.tool_register)
            plugin.register_hooks(self.hooks)
            plugin.attach_prompt(self.context)

        logger.debug(f"Registered tools: {[t.name for t in self.tool_register.list_tools()]}")

    # ------------------------------------------------------------------
    # Plugin access + runtime capability toggles (called by control_center)
    # ------------------------------------------------------------------

    def get_plugin(self, name: str) -> "Plugin | None":
        """Return the plugin with the given name, or None."""
        for p in self.plugins:
            if p.name == name:
                return p
        return None

    async def enable_browser_use(self) -> None:
        plugin = self.get_plugin("browser_use")
        if plugin:
            await plugin.enable()

    async def disable_browser_use(self) -> None:
        plugin = self.get_plugin("browser_use")
        if plugin:
            await plugin.disable()

    async def enable_computer_use(self) -> None:
        plugin = self.get_plugin("computer_use")
        if plugin:
            await plugin.enable()

    async def disable_computer_use(self) -> None:
        plugin = self.get_plugin("computer_use")
        if plugin:
            await plugin.disable()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(
        self,
        message: "HumanMessage | ImageMessage",
        session_id: str,
        incoming: "IncomingMessage | None" = None,
        publish_stream: "Callable[..., Awaitable[None]] | None" = None,
        pending_replies: "dict | None" = None,
        prompt_mode: PromptMode | None = None,
        system_prompt: str | None = None,
    ) -> AIMessage:
        """Run the agentic loop for one message and return the AIMessage response.

        Args:
            message: Pre-built HumanMessage or ImageMessage (STT already applied).
            session_id: Unique session identifier (e.g. "telegram:12345").
            incoming: Original IncomingMessage for per-message tool extensions.
            publish_stream: Callback for streaming chunks. If None, non-streaming.
            pending_replies: Shared dict for tools that wait for a user reply.
        """
        # Set per-message tool extensions
        if incoming:
            self.tool_register.set_extension("_channel", incoming.channel)
            self.tool_register.set_extension("_chat_id", incoming.chat_id)
            self.tool_register.set_extension("_account_id", incoming.account_id)
            self.tool_register.set_extension("_metadata", incoming.metadata or {})
            self.tool_register.set_extension("_session_id", session_id)
            from operator_use.bus.views import ImagePart as _ImagePart

            _img_paths = [
                p
                for part in incoming.parts
                if isinstance(part, _ImagePart)
                for p in (part.paths or [])
            ]
            self.tool_register.set_extension("_incoming_image_paths", _img_paths or None)
        if pending_replies is not None:
            self.tool_register.set_extension("_pending_replies", pending_replies)

        session = self.sessions.get_or_create(session_id=session_id)
        session.add_message(message)

        await self.hooks.emit(
            HookEvent.BEFORE_AGENT_START,
            BeforeAgentStartContext(message=incoming, session=session),
        )

        effective_mode = prompt_mode if prompt_mode is not None else self.prompt_mode
        effective_prompt = system_prompt if system_prompt is not None else self.system_prompt
        if publish_stream is not None:
            response_message = await self._loop_stream(
                session=session,
                publish_stream=publish_stream,
                message=incoming,
                prompt_mode=effective_mode,
                system_prompt=effective_prompt or None,
            )
        else:
            response_message = await self._loop(
                session=session,
                message=incoming,
                prompt_mode=effective_mode,
                system_prompt=effective_prompt or None,
            )

        # Allow hooks to modify the final response before saving
        end_ctx = await self.hooks.emit(
            HookEvent.BEFORE_AGENT_END,
            BeforeAgentEndContext(message=incoming, session=session, response=response_message),
        )
        response_message = end_ctx.response

        self.sessions.save(session)

        await self.hooks.emit(
            HookEvent.AFTER_AGENT_END,
            AfterAgentEndContext(message=incoming, session=session, response=response_message),
        )

        return response_message

    # ------------------------------------------------------------------
    # Reaction handling (no LLM call — update metadata only)
    # ------------------------------------------------------------------

    async def _handle_reaction(self, message: IncomingMessage) -> None:
        """Add or remove a user reaction on the target AIMessage in the session."""
        session_id = f"{message.channel}:{message.chat_id}"
        bot_message_id = message.metadata.get("_reaction_bot_message_id")
        emojis = message.metadata.get("_reaction_emojis", [])
        removed_emojis = message.metadata.get("_reaction_removed_emojis", [])
        user_id = message.metadata.get("user_id")

        session = self.sessions.get_or_create(session_id=session_id)
        for msg in reversed(session.messages):
            if isinstance(msg, AIMessage) and msg.metadata.get("message_id") == bot_message_id:
                if not isinstance(msg.metadata, dict):
                    msg.metadata = {}
                reactions: list = msg.metadata.setdefault("reactions", [])

                if removed_emojis:
                    removed_set = set(removed_emojis)
                    msg.metadata["reactions"] = [
                        r
                        for r in reactions
                        if not (
                            r.get("user_id") == user_id and set(r.get("emojis", [])) & removed_set
                        )
                    ]
                    logger.info(
                        "Removed reaction %s from bot message %s", removed_emojis, bot_message_id
                    )

                if emojis:
                    msg.metadata["reactions"].append(
                        {
                            "emojis": emojis,
                            "user_id": user_id,
                            "timestamp": datetime.now().isoformat(),
                        }
                    )
                    logger.info("Stored reaction %s on bot message %s", emojis, bot_message_id)
                break
        self.sessions.save(session)

    # ------------------------------------------------------------------
    # LLM loops
    # ------------------------------------------------------------------

    async def _prepare_messages(
        self,
        session: Session,
        message: "IncomingMessage | None",
        prompt_mode: PromptMode = PromptMode.FULL,
        system_prompt: str | None = None,
    ) -> list:
        """Build the LLM message list: system prompt + history."""
        is_voice = (
            any(type(p).__name__ == "AudioPart" for p in (message.parts or []))
            if message
            else False
        )
        return await self.context.build_messages(
            history=session.get_history(),
            is_voice=is_voice,
            session_id=session.id,
            prompt_mode=prompt_mode,
            system_prompt=system_prompt,
        )

    async def _execute_tool(
        self,
        tool_call,
        thinking,
        thinking_signature,
        session: Session,
        error_messages: list[ToolMessage],
    ):
        """Execute a tool call and manage error accumulation.

        Failed tool calls are added to error_messages list.
        Successful tool calls are added to session, and error_messages are cleared.
        """
        # Format tool call nicely: tool_name(param1=value1, param2=value2, ...)
        params_str = ", ".join(f"{k}={repr(v)[:50]}" for k, v in tool_call.params.items())
        logger.info(f"Tool call | {tool_call.name}({params_str})")

        pre_ctx = await self.hooks.emit(
            HookEvent.BEFORE_TOOL_CALL,
            BeforeToolCallContext(session=session, tool_call=tool_call),
        )
        if pre_ctx.skip:
            tool_result = pre_ctx.result
            content = tool_result.output if tool_result.success else tool_result.error
        else:
            tool_result = await self.tool_register.aexecute(tool_call.name, tool_call.params)
            content = tool_result.output if tool_result.success else tool_result.error

        if tool_result.success:
            logger.info(f"Tool success | {str(content)[:200]}")
        else:
            logger.warning(f"Tool error | {str(content)[:200]}")

        await self.hooks.emit(
            HookEvent.AFTER_TOOL_CALL,
            AfterToolCallContext(
                session=session, tool_call=tool_call, tool_result=tool_result, content=content
            ),
        )

        tool_message = ToolMessage(
            id=tool_call.id,
            name=tool_call.name,
            params=tool_call.params,
            content=content,
            thinking=thinking,
            thinking_signature=thinking_signature,
        )

        # Handle error accumulation and cleanup
        if tool_result.success:
            # Success: add to session and clear accumulated errors
            session.add_message(tool_message)
            num_errors = len(error_messages)
            error_messages.clear()
            if num_errors > 0:
                logger.info(f"Tool success - cleared {num_errors} accumulated error messages")
        else:
            # Failure: add to error_messages list for feedback during retries
            error_messages.append(tool_message)
            logger.info(f"Tool added to error queue | accumulated errors: {len(error_messages)}")

        return tool_result, content

    @staticmethod
    def _clean_content(content: str) -> str:
        """Strip <think> blocks, leading message IDs, and control tags from LLM output."""
        import re

        content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL)
        content = re.sub(r"^\[(bot_)?msg_id:\d+\]\s*", "", content)
        content = re.sub(r"<ctrl\d+>", "", content)
        return content.strip() or "(no response)"

    async def _loop(
        self,
        session: Session,
        message: "IncomingMessage | None" = None,
        prompt_mode: PromptMode = PromptMode.FULL,
        system_prompt: str | None = None,
    ) -> AIMessage:
        """Non-streaming agentic loop."""
        tools = self.tool_register.list_tools()
        error_messages: list[ToolMessage] = []  # Accumulate tool errors for feedback during retries

        for iteration in range(self.max_iterations):
            # Build messages: session history + accumulated errors (for LLM feedback)
            messages = await self._prepare_messages(session, message, prompt_mode, system_prompt)
            # Include error_messages so LLM sees accumulated failures during retries
            if error_messages:
                messages.extend(error_messages)

            logger.info(
                f"LLM call | model={self.llm.model_name} messages={len(messages)} tools={len(tools)}"
            )
            if iteration == 0 and message:
                await self.hooks.emit(
                    HookEvent.AFTER_AGENT_START,
                    AfterAgentStartContext(message=message, session=session, iteration=iteration),
                )
            before_llm_ctx = await self.hooks.emit(
                HookEvent.BEFORE_LLM_CALL,
                BeforeLLMCallContext(session=session, messages=messages, iteration=iteration),
            )
            messages = before_llm_ctx.messages

            try:
                llm_event = await self.llm.ainvoke(messages=messages, tools=tools)
            except Exception as e:
                logger.error(f"LLM call failed | {e}")
                llm_event = LLMEvent(type=LLMEventType.ERROR, error=str(e))

            after_llm_ctx = await self.hooks.emit(
                HookEvent.AFTER_LLM_CALL,
                AfterLLMCallContext(
                    session=session, messages=messages, event=llm_event, iteration=iteration
                ),
            )
            llm_event = after_llm_ctx.event

            logger.info(f"LLM response | {llm_event.type.name}")
            thinking, thinking_signature = (
                (llm_event.thinking.content, llm_event.thinking.signature)
                if llm_event.thinking
                else (None, None)
            )
            match llm_event.type:
                case LLMEventType.TOOL_CALL:
                    tool_result, content = await self._execute_tool(
                        llm_event.tool_call, thinking, thinking_signature, session, error_messages
                    )
                    if tool_result.metadata and tool_result.metadata.get("stop_loop"):
                        return AIMessage(content=content or "")
                case LLMEventType.TEXT:
                    clean = self._clean_content(llm_event.content or "")
                    logger.info(f"Response | {clean[:120]!r}{'...' if len(clean) > 120 else ''}")
                    msg = AIMessage(
                        content=clean, thinking=thinking, thinking_signature=thinking_signature
                    )
                    session.add_message(msg)
                    return msg
                case LLMEventType.ERROR:
                    error_text = llm_event.error or "Unknown LLM error"
                    logger.error(f"LLM error event | {error_text}")
                    msg = AIMessage(content=f"Sorry, I encountered an error: {error_text}")
                    session.add_message(msg)
                    return msg
        raise RuntimeError(f"Agent exceeded max_iterations ({self.max_iterations})")

    async def _loop_stream(
        self,
        session: Session,
        publish_stream: "Callable[..., Awaitable[None]]",
        message: "IncomingMessage | None" = None,
        prompt_mode: PromptMode = PromptMode.FULL,
        system_prompt: str | None = None,
    ) -> AIMessage:
        """Streaming agentic loop."""
        tools = self.tool_register.list_tools()
        error_messages: list[ToolMessage] = []  # Accumulate tool errors for feedback during retries

        for iteration in range(self.max_iterations):
            # Build messages: session history + accumulated errors (for LLM feedback)
            messages = await self._prepare_messages(session, message, prompt_mode, system_prompt)
            # Include error_messages so LLM sees accumulated failures during retries
            if error_messages:
                messages.extend(error_messages)

            thinking = None
            thinking_signature = None
            content = ""
            last_publish_len = 0
            stream_init_sent = False
            chunk_size = 15

            if iteration == 0 and message:
                await self.hooks.emit(
                    HookEvent.AFTER_AGENT_START,
                    AfterAgentStartContext(message=message, session=session, iteration=iteration),
                )

            before_llm_ctx = await self.hooks.emit(
                HookEvent.BEFORE_LLM_CALL,
                BeforeLLMCallContext(session=session, messages=messages, iteration=iteration),
            )
            messages = before_llm_ctx.messages

            try:
                async for event in self.llm.astream(messages=messages, tools=tools):
                    if event.thinking:
                        thinking = event.thinking.content
                        thinking_signature = event.thinking.signature

                    match event.type:
                        case LLMStreamEventType.TEXT_START:
                            pass
                        case LLMStreamEventType.TOOL_CALL:
                            await self.hooks.emit(
                                HookEvent.AFTER_LLM_CALL,
                                AfterLLMCallContext(
                                    session=session,
                                    messages=messages,
                                    event=LLMEvent(
                                        type=LLMEventType.TOOL_CALL, tool_call=event.tool_call
                                    ),
                                    iteration=iteration,
                                ),
                            )
                            tool_result, content = await self._execute_tool(
                                event.tool_call, thinking, thinking_signature, session, error_messages
                            )
                            if tool_result.metadata and tool_result.metadata.get("stop_loop"):
                                return AIMessage(content=content or "")
                            break
                        case LLMStreamEventType.TEXT_DELTA:
                            if event.content:
                                content += event.content
                                if len(content) - last_publish_len >= chunk_size:
                                    await publish_stream(content, False, init=not stream_init_sent)
                                    stream_init_sent = True
                                    last_publish_len = len(content)
                        case LLMStreamEventType.ERROR:
                            error_text = event.content or "Unknown LLM error"
                            logger.error(f"LLM stream error | {error_text}")
                            await publish_stream(f"Sorry, I encountered an error: {error_text}", True, init=not stream_init_sent)
                            msg = AIMessage(content=f"Sorry, I encountered an error: {error_text}")
                            session.add_message(msg)
                            return msg
                        case LLMStreamEventType.TEXT_END:
                            logger.info(
                                f"Response | {content[:120]!r}{'...' if len(content) > 120 else ''} usage={event.usage}"
                            )
                            content = self._clean_content(content or "(no response)")
                            await self.hooks.emit(
                                HookEvent.AFTER_LLM_CALL,
                                AfterLLMCallContext(
                                    session=session,
                                    messages=messages,
                                    event=LLMEvent(
                                        type=LLMEventType.TEXT,
                                        content=content,
                                        thinking=Thinking(
                                            content=thinking, signature=thinking_signature
                                        )
                                        if thinking
                                        else None,
                                        usage=event.usage,
                                    ),
                                    iteration=iteration,
                                ),
                            )
                            if not stream_init_sent:
                                await publish_stream(content, False, init=True)
                            await publish_stream(content, True, init=False)
                            msg = AIMessage(
                                content=content,
                                thinking=thinking,
                                thinking_signature=thinking_signature,
                            )
                            session.add_message(msg)
                            return msg
            except Exception as e:
                logger.error(f"LLM stream failed | {e}")
                error_text = str(e)
                await publish_stream(f"Sorry, I encountered an error: {error_text}", True, init=not stream_init_sent)
                msg = AIMessage(content=f"Sorry, I encountered an error: {error_text}")
                session.add_message(msg)
                return msg
        raise RuntimeError(f"Agent exceeded max_iterations ({self.max_iterations})")

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from acp import (
    PROTOCOL_VERSION,
    PromptResponse,
    run_agent,
    start_tool_call,
    update_agent_message_text,
    update_agent_thought_text,
)
from acp.schema import (
    AgentCapabilities,
    AuthenticateResponse,
    Implementation,
    InitializeResponse,
    LoadSessionResponse,
    NewSessionResponse,
    SessionCapabilities,
)

from program.acp.utils import text_from_content_blocks

if TYPE_CHECKING:
    from program.runtime.service import Runtime
    from program.agent.service import Agent

logger = logging.getLogger(__name__)

_AGENT_NAME = 'Operator'
_AGENT_VERSION = '1.0.0'


class OperatorACPAgent:
    """
    ACP agent backed by the Operator runtime.

    One instance handles all ACP sessions. Each session/new call creates
    an isolated Agent via runtime.create_session_agent() keyed by session_id.

    Usage:
        agent = OperatorACPAgent(runtime)
        await acp.run_agent(agent)   # serves over stdio until stdin closes
    """

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._sessions: dict[str, Agent] = {}
        self._connection: Any = None  # AgentSideConnection — set via on_connect

    # ── ACP lifecycle ─────────────────────────────────────────────────────────

    def on_connect(self, connection: Any) -> None:
        """Called by SDK with the AgentSideConnection before any messages arrive."""
        self._connection = connection

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: Any = None,
        client_info: Any = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_info=Implementation(name=_AGENT_NAME, version=_AGENT_VERSION),
            agent_capabilities=AgentCapabilities(
                load_session=True,
                session_capabilities=SessionCapabilities(
                    close=True,
                    list=True,
                    resume=True,
                ),
            ),
        )

    async def authenticate(self, method_id: str, **kwargs: Any) -> AuthenticateResponse:
        # Token / provenance validation is handled at the transport layer.
        return AuthenticateResponse()

    async def new_session(
        self,
        cwd: str,
        additional_directories: Any = None,
        mcp_servers: Any = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        session_id = str(uuid.uuid4())
        agent = self._runtime.create_session_agent()
        self._sessions[session_id] = agent
        logger.info('ACP: new session %s', session_id)
        return NewSessionResponse(session_id=session_id)

    async def load_session(self, session_id: str, **kwargs: Any) -> LoadSessionResponse:
        if session_id not in self._sessions:
            self._sessions[session_id] = self._runtime.create_session_agent()
            logger.info('ACP: resumed session %s (new agent)', session_id)
        return LoadSessionResponse()

    async def prompt(
        self,
        prompt: list,
        session_id: str,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> PromptResponse:
        from program.agent.types import PromptOptions
        from program.hooks.types import (
            MessageUpdateEvent,
            ToolExecutionStartEvent,
        )
        from program.message.types import Role

        agent = self._sessions.get(session_id)
        if agent is None:
            agent = self._runtime.create_session_agent()
            self._sessions[session_id] = agent

        text = text_from_content_blocks(prompt)
        connection = self._connection

        async def _on_event(event) -> None:
            if connection is None:
                return
            match event:
                case MessageUpdateEvent(message=m) if m.role == Role.ASSISTANT:
                    for content in m.contents:
                        chunk = getattr(content, 'content', '')
                        kind = getattr(content, 'type', '')
                        if not chunk:
                            continue
                        if kind == 'text':
                            await connection.session_update(
                                session_id, update_agent_message_text(chunk)
                            )
                        elif kind == 'thinking':
                            await connection.session_update(
                                session_id, update_agent_thought_text(chunk)
                            )
                case ToolExecutionStartEvent(tool_call=tc):
                    await connection.session_update(
                        session_id,
                        start_tool_call(
                            tool_call_id=tc.id,
                            title=tc.name,
                            raw_input=tc.args,
                        ),
                    )

        unsub = agent.hooks.subscribe(_on_event)
        try:
            await agent.invoke(text, PromptOptions(source='interactive'))
        except Exception:
            logger.exception('ACP: agent.invoke failed for session %s', session_id)
        finally:
            unsub()

        return PromptResponse(stop_reason='end_turn', user_message_id=message_id)

    async def close_session(self, session_id: str, **kwargs: Any) -> None:
        self._sessions.pop(session_id, None)
        logger.info('ACP: closed session %s', session_id)

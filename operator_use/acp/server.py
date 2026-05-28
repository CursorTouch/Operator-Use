from __future__ import annotations

import logging
import uuid
from pathlib import Path
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
    ForkSessionResponse,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    NewSessionResponse,
    ResumeSessionResponse,
    SessionCapabilities,
    SessionCloseCapabilities,
    SessionListCapabilities,
    SessionResumeCapabilities,
)

from operator_use.acp.utils import text_from_content_blocks

if TYPE_CHECKING:
    from operator_use.runtime.service import Runtime
    from operator_use.agent.service import Agent

logger = logging.getLogger(__name__)

_AGENT_NAME = 'Operator'
_AGENT_VERSION = '1.0.0'


class ACPAgent:
    """
    ACP agent backed by the Operator runtime.

    One instance handles all ACP sessions. Each session creates an isolated
    Agent keyed by session_id. When *acp_sessions_dir* is provided, session
    history is written to <acp_sessions_dir>/<session_id>.jsonl so conversations
    survive restarts (server-side persistence).

    Usage:
        agent = ACPAgent(runtime, acp_sessions_dir=profile.acp_sessions_dir)
        await acp.run_agent(agent)   # serves over stdio until stdin closes
    """

    def __init__(self, runtime: Runtime, acp_sessions_dir: Path | None = None) -> None:
        self._runtime = runtime
        self._acp_sessions_dir = acp_sessions_dir
        self._sessions: dict[str, Agent] = {}
        self._connection: Any = None  # AgentSideConnection — set via on_connect

    # ── ACP lifecycle ─────────────────────────────────────────────────────────

    def on_connect(self, conn: Any) -> None:
        """Called by SDK with the AgentSideConnection before any messages arrive."""
        self._connection = conn

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
                    close=SessionCloseCapabilities(),
                    list=SessionListCapabilities(),
                    resume=SessionResumeCapabilities(),
                ),
            ),
        )

    async def authenticate(self, method_id: str, **kwargs: Any) -> AuthenticateResponse:
        # Token / provenance validation is handled at the transport layer.
        return AuthenticateResponse()

    def _make_agent(self, session_id: str) -> Agent:
        """Create a session agent — persistent if acp_sessions_dir is set, otherwise in-memory."""
        if self._acp_sessions_dir is not None:
            return self._runtime.create_acp_session_agent(self._acp_sessions_dir, session_id)
        return self._runtime.create_session_agent()

    async def new_session(
        self,
        cwd: str,
        additional_directories: Any = None,
        mcp_servers: Any = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        session_id = str(uuid.uuid4())
        try:
            agent = self._make_agent(session_id)
        except Exception:
            logger.exception('ACP: failed to create agent for new session %s', session_id)
            raise
        self._sessions[session_id] = agent
        logger.info('ACP: new session %s (persist=%s)', session_id, self._acp_sessions_dir is not None)
        return NewSessionResponse(session_id=session_id)

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        additional_directories: list[str] | None = None,
        mcp_servers: Any = None,
        **kwargs: Any,
    ) -> LoadSessionResponse:
        if session_id not in self._sessions:
            try:
                agent = self._make_agent(session_id)
            except Exception:
                logger.exception('ACP: failed to create agent for resumed session %s', session_id)
                raise
            self._sessions[session_id] = agent
            logger.info('ACP: loaded session %s', session_id)
        return LoadSessionResponse()

    async def prompt(
        self,
        prompt: list,
        session_id: str,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> PromptResponse:
        from operator_use.agent.types import PromptOptions
        from operator_use.hooks.types import (
            MessageUpdateEvent,
            ToolExecutionStartEvent,
        )
        from operator_use.message.types import Role

        agent = self._sessions.get(session_id)
        if agent is None:
            agent = self._make_agent(session_id)
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

    async def list_sessions(
        self,
        additional_directories: list[str] | None = None,
        cursor: str | None = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> ListSessionsResponse:
        return ListSessionsResponse(sessions=[])

    async def set_session_mode(self, mode_id: str, session_id: str, **kwargs: Any) -> None:
        return None

    async def set_session_model(self, model_id: str, session_id: str, **kwargs: Any) -> None:
        return None

    async def set_config_option(self, config_id: str, session_id: str, value: str | bool, **kwargs: Any) -> None:
        return None

    async def fork_session(
        self,
        cwd: str,
        session_id: str,
        additional_directories: list[str] | None = None,
        mcp_servers: Any = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        # Fork creates a brand-new independent session (no history copy).
        new_id = str(uuid.uuid4())
        try:
            agent = self._make_agent(new_id)
        except Exception:
            logger.exception('ACP: failed to create agent for fork %s → %s', session_id, new_id)
            raise
        self._sessions[new_id] = agent
        logger.info('ACP: forked session %s → %s', session_id, new_id)
        return ForkSessionResponse(session_id=new_id)

    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        additional_directories: list[str] | None = None,
        mcp_servers: Any = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        if session_id not in self._sessions:
            try:
                agent = self._make_agent(session_id)
            except Exception:
                logger.exception('ACP: failed to create agent for resumed session %s', session_id)
                raise
            self._sessions[session_id] = agent
            logger.info('ACP: resume_session loaded %s', session_id)
        return ResumeSessionResponse()

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        pass

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        pass

    async def close_session(self, session_id: str, **kwargs: Any) -> None:
        self._sessions.pop(session_id, None)
        logger.info('ACP: closed session %s', session_id)

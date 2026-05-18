"""acp_agent tool — call remote ACP agents (Claude Code, Codex, etc.) asynchronously.

Tasks are dispatched non-blocking: the tool returns immediately and the
result arrives as an IncomingMessage via the bus (gateway mode) or is
injected directly into the agent (CLI mode).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator

from program.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

if TYPE_CHECKING:
    from program.acp.manager import ACPSessionManager
    from program.auth.acp import ACPAuthManager
    from program.bus.service import Bus
    from program.agent.service import Agent
    from program.acp.types import ACPAgentConfig

logger = logging.getLogger(__name__)


class _ACPSchema(BaseModel):
    action: Literal['agents', 'run', 'sessions'] = Field(
        description=(
            'agents   — list all registered ACP agents from settings.\n'
            'run      — send a task to a remote agent (non-blocking; result arrives as a message).\n'
            'sessions — show currently persisted ACP sessions.'
        )
    )
    agent: str | None = Field(
        default=None,
        description='Agent name to run the task on (required for action=run).',
    )
    task: str | None = Field(
        default=None,
        description='The task/prompt to send to the remote agent (required for action=run).',
    )

    @model_validator(mode='after')
    def _validate(self) -> _ACPSchema:
        if self.action == 'run':
            if not self.agent:
                raise ValueError("agent is required for action='run'")
            if not self.task:
                raise ValueError("task is required for action='run'")
        return self


class ACPAgentTool(Tool):
    """Call remote ACP agents non-blocking; results are delivered as bus messages."""

    def __init__(
        self,
        registry: list[ACPAgentConfig],
        session_manager: ACPSessionManager,
        auth_manager: ACPAuthManager,
        bus: Bus | None,
        agent: Agent | None,
    ) -> None:
        super().__init__(
            name='acp_agent',
            description=(
                'Interact with registered remote ACP agents (Claude Code, Codex CLI, etc.).\n\n'
                "  agents                          — list registered agents\n"
                "  run, agent='<name>', task='...' — dispatch a task (non-blocking)\n"
                "  sessions                        — view active session state\n\n"
                'The result of a dispatched task arrives as a follow-up message — '
                'the current agent loop is NOT blocked.'
            ),
            schema=_ACPSchema,
            kind=ToolKind.Execute,
            execution_mode=ToolExecutionMode.Sequential,
        )
        self._registry: dict[str, ACPAgentConfig] = {a.name: a for a in registry}
        self._session_manager = session_manager
        self._auth = auth_manager
        self._bus = bus
        self._agent = agent

    # ── Tool entry point ──────────────────────────────────────────────────────

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = _ACPSchema.model_validate(invocation.input)

        if params.action == 'agents':
            return ToolResult(content=self._list_agents())

        if params.action == 'sessions':
            return ToolResult(content=self._list_sessions())

        # action == 'run'
        name = params.agent
        config = self._registry.get(name)  # type: ignore[arg-type]
        if config is None:
            known = ', '.join(self._registry) or '(none configured)'
            return ToolResult(
                content=f"Agent '{name}' is not in the registry. Registered agents: {known}",
                is_error=True,
            )

        # Capture channel/chat_id from contextvar before spawning
        from program.subagent.manager import _session_channel, _session_chat_id
        channel = _session_channel.get()
        chat_id = _session_chat_id.get()

        asyncio.create_task(
            self._run_task(config, params.task, channel, chat_id)  # type: ignore[arg-type]
        )
        return ToolResult(
            content=(
                f"Task dispatched to '{name}'. "
                "The result will arrive as a follow-up message when the agent finishes."
            )
        )

    # ── List helpers ──────────────────────────────────────────────────────────

    def _list_agents(self) -> str:
        if not self._registry:
            return 'No ACP agents configured. Add entries to `acp_agents` in settings.json.'
        lines = ['Registered ACP agents:']
        for name, cfg in self._registry.items():
            has_token = self._auth.has_token(name)
            session = self._session_manager.get(name)
            token_status = 'authenticated' if has_token else 'no credentials'
            session_status = f"session {session['session_id'][:8]}..." if session else 'no session'
            transport = f"{cfg.transport}:{cfg.command or cfg.url or 'discover'}"
            lines.append(f"  {name}  [{transport}]  {token_status}  {session_status}")
        return '\n'.join(lines)

    def _list_sessions(self) -> str:
        sessions = self._session_manager.list()
        if not sessions:
            return 'No active ACP sessions.'
        lines = ['Active ACP sessions:']
        for s in sessions:
            lines.append(
                f"  {s['agent_name']}  session={s['session_id'][:12]}..."
                f"  last_used={s.get('last_used_at', '?')[:19]}"
            )
        return '\n'.join(lines)

    # ── Non-blocking task runner ──────────────────────────────────────────────

    async def _run_task(
        self,
        config: ACPAgentConfig,
        task: str,
        channel: str | None,
        chat_id: str | None,
    ) -> None:
        from program.acp.client import ACPClient

        logger.info('ACP task start | agent=%s transport=%s', config.name, config.transport)
        result_text: str
        try:
            if config.transport == 'stdio':
                if not config.command:
                    raise ValueError(f"ACP agent '{config.name}' requires a command for stdio transport")
                client = ACPClient.stdio(config.command, *config.args)
            elif config.transport == 'http':
                if not config.url:
                    raise ValueError(f"ACP agent '{config.name}' requires a url for http transport")
                client = ACPClient.http(config.url)
            else:
                client = ACPClient.discover(config.name)

            async with client as c:
                async with c.session() as session_id:
                    self._session_manager.save(config.name, session_id)
                    result_text = await c.run(task, session_id)

            logger.info('ACP task done | agent=%s', config.name)
            content = f"[{config.name}]\n{result_text}"

        except Exception as exc:
            logger.exception('ACP task failed | agent=%s', config.name)
            content = f"[{config.name}] Error: {exc}"

        await self._deliver(content, channel, chat_id)

    async def _deliver(
        self,
        content: str,
        channel: str | None,
        chat_id: str | None,
    ) -> None:
        if channel and chat_id and self._bus is not None:
            from program.bus.types import IncomingMessage, TextPart
            msg = IncomingMessage(
                channel=channel,
                chat_id=chat_id,
                parts=[TextPart(content=content)],
            )
            await self._bus.publish_incoming(msg)
        elif self._agent is not None:
            from program.agent.types import PromptOptions
            await self._agent.invoke(content, PromptOptions(source='acp'))

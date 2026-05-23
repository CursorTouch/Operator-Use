"""acp_agent tool — call remote ACP agents (Claude Code, Codex, etc.) asynchronously.

Tasks are dispatched non-blocking: the tool returns immediately and the
result arrives as an IncomingMessage via the bus (gateway mode) or is
injected directly into the agent (CLI mode).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, model_validator

from program.tool.types import (
    Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult,
    ToolExecutionUpdateCallback, AbortSignal,
)

if TYPE_CHECKING:
    from program.acp.manager import ACPSessionManager
    from program.auth.acp import ACPAuthManager
    from program.bus.service import Bus
    from program.agent.service import Agent
    from program.acp.types import ACPAgentConfig

logger = logging.getLogger(__name__)

# Built-in agents available without any settings.json configuration.
# "claude" spawns an Operator child process as an ACP server — it inherits
# the parent's environment (API keys, model settings) automatically.
_BUILTIN_AGENTS: dict[str, ACPAgentConfig] = {}

def _init_builtins() -> None:
    import shutil
    from program.acp.types import ACPAgentConfig as _Cfg
    if shutil.which('operator'):
        _BUILTIN_AGENTS['claude'] = _Cfg(
            name='claude', transport='stdio', command='operator', args=['acp', 'serve']
        )

_init_builtins()


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
        settings_manager: Any = None,
    ) -> None:
        super().__init__(
            name='acp_agent',
            description=(
                'Interact with ACP agents (built-in or configured in settings).\n\n'
                "  agents                          — list available agents\n"
                "  run, agent='<name>', task='...' — dispatch a task (non-blocking)\n"
                "  sessions                        — view active session state\n\n"
                "Built-in agents (always available):\n"
                "  claude  — spawns an Operator child via 'operator acp serve' (full tool access)\n\n"
                'The result of a dispatched task arrives as a follow-up message — '
                'the current agent loop is NOT blocked.'
            ),
            schema=_ACPSchema,
            kind=ToolKind.Execute,
            execution_mode=ToolExecutionMode.Sequential,
        )
        self._registry: dict[str, ACPAgentConfig] = {**_BUILTIN_AGENTS, **{a.name: a for a in registry}}
        self._session_manager = session_manager
        self._auth = auth_manager
        self._bus = bus
        self._agent = agent
        self._settings_manager = settings_manager

    # ── Tool entry point ──────────────────────────────────────────────────────

    async def execute(self, invocation: ToolInvocation, tool_execution_update_callback: ToolExecutionUpdateCallback | None = None, signal: AbortSignal | None = None) -> ToolResult:
        params = _ACPSchema.model_validate(invocation.params)

        if params.action == 'agents':
            return ToolResult.ok(invocation.id, self._list_agents())

        if params.action == 'sessions':
            return ToolResult.ok(invocation.id, self._list_sessions())

        # action == 'run'
        name = params.agent
        config = self._registry.get(name)  # type: ignore[arg-type]
        if config is None:
            known = ', '.join(self._registry) or '(none configured)'
            return ToolResult.error(
                invocation.id,
                f"Agent '{name}' is not in the registry. Registered agents: {known}",
            )

        # Capture channel/chat_id from contextvar before spawning
        from program.subagent.manager import _session_channel, _session_chat_id
        channel = _session_channel.get()
        chat_id = _session_chat_id.get()
        caller_agent = self._agent

        asyncio.create_task(
            self._run_task(config, params.task, channel, chat_id, caller_agent)  # type: ignore[arg-type]
        )
        return ToolResult.ok(
            invocation.id,
            f"Task dispatched to '{name}'. "
            "The result will arrive as a follow-up message when the agent finishes.",
        )

    # ── List helpers ──────────────────────────────────────────────────────────

    def _list_agents(self) -> str:
        if not self._registry:
            return 'No ACP agents configured. Add entries to `acp.agents` in settings.json.'
        lines = ['Registered ACP agents:']
        for name, cfg in self._registry.items():
            has_token = self._auth.has_token(name)
            session = self._session_manager.get(name)
            token_status = 'authenticated' if has_token else 'no credentials'
            session_status = f"session {session['session_id'][:8]}..." if session else 'no session'
            target = cfg.command or cfg.url or 'configured'
            transport = f"{cfg.transport}:{target}"
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
        caller_agent: Agent | None,
    ) -> None:
        from program.acp.client import ACPClient

        logger.info('ACP task start | agent=%s transport=%s', config.name, config.transport)
        result_text: str
        try:
            if config.transport == 'stdio':
                if not config.command:
                    raise ValueError(f"ACP agent '{config.name}' requires a command for stdio transport")
                args = list(config.args)
                if config.name in _BUILTIN_AGENTS and self._settings_manager is not None:
                    provider = self._settings_manager.get_default_provider()
                    model = self._settings_manager.get_default_model()
                    if not provider:
                        # No explicit default — prefer anthropic-claude-code, then first authenticated.
                        try:
                            import json as _json
                            from program.settings.paths import get_providers_auth_path
                            _p = get_providers_auth_path()
                            if _p.exists():
                                _creds = _json.loads(_p.read_text())
                                if _creds:
                                    provider = 'anthropic-claude-code' if 'anthropic-claude-code' in _creds else next(iter(_creds))
                        except Exception:
                            pass
                    if not model and provider:
                        # Pick the first registered model for this provider.
                        try:
                            from program.inference.model.registry import ModelRegistry
                            _reg = ModelRegistry.from_llm_builtins()
                            for _candidates in _reg._models.values():
                                for _m in (_candidates if isinstance(_candidates, list) else [_candidates]):
                                    if getattr(_m, 'provider', None) == provider:
                                        model = _m.id
                                        break
                                if model:
                                    break
                        except Exception:
                            pass
                    if provider:
                        args += ['--provider', provider]
                    if model:
                        args += ['--model', model]
                client = ACPClient.stdio(config.command, *args)
            elif config.transport == 'http':
                if not config.url:
                    raise ValueError(f"ACP agent '{config.name}' requires a url for http transport")
                token = self._auth.get_token(config.name)
                client = ACPClient.http(config.url, token=token)
            elif config.transport == 'webrtc':
                if not config.url:
                    raise ValueError(f"ACP agent '{config.name}' requires a room in url for webrtc transport")
                client = ACPClient.webrtc(config.url)
            else:
                raise ValueError(f"Unknown ACP transport {config.transport!r} for agent '{config.name}'")

            async with client as c:
                async with c.session() as session_id:
                    self._session_manager.save(config.name, session_id)
                    result_text = await c.run(task, session_id)

            logger.info('ACP task done | agent=%s', config.name)
            content = (
                f"Agent: {config.name}\n\n"
                f"Task:\n{task}\n\n"
                f"Response:\n{result_text}"
            )

        except Exception as exc:
            logger.exception('ACP task failed | agent=%s', config.name)
            content = f"Agent '{config.name}' failed with error: {exc}"

        await self._deliver(content, channel, chat_id, caller_agent)

    async def _deliver(
        self,
        content: str,
        channel: str | None,
        chat_id: str | None,
        caller_agent: Agent | None,
    ) -> None:
        if channel and chat_id and self._bus is not None:
            from program.bus.types import IncomingMessage, TextPart
            msg = IncomingMessage(
                channel=channel,
                chat_id=chat_id,
                parts=[TextPart(content=content)],
                user_id='acp_agent',
                metadata={'source': 'acp_agent', 'target_agent': caller_agent},
            )
            await self._bus.publish_incoming(msg)
        elif self._agent is not None:
            from program.agent.types import PromptOptions
            await self._agent.invoke(content, PromptOptions(source='subagent'))

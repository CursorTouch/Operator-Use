"""acp_agent tool — call remote ACP agents (Claude Code, Codex, etc.).

Actions
-------
agents   — list configured agents + auth / session status.
run      — send a task.  detached=False blocks and returns the result inline;
           detached=True (default) fires in the background and delivers the
           result as a follow-up message.
spawn    — create a named persistent session with an agent; returns session_id.
           Reuse that session_id in run/send to continue the conversation.
send     — send a follow-up into an existing session (requires session_id).
sessions — list all persisted ACP session bookmarks.
status   — check a detached run by task_id.
cancel   — stop a running detached task by task_id.

Session model
-------------
Each profile stores one bookmark per remote agent in:
    profiles/<name>/acp/<agent>.json   { session_id, last_used_at, ... }

spawn creates a fresh session and saves the bookmark.
run/send with an existing bookmark automatically resume that session on
HTTP/WebRTC transports (where the server is long-running).  For stdio agents,
each call spawns a new subprocess so the server-side session_id is always fresh;
the bookmark is updated but context doesn't carry over.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional
from weakref import WeakKeyDictionary

from pydantic import BaseModel, Field

from operator_use.tool.types import (
    Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult,
    ToolExecutionUpdateCallback, AbortSignal, ToolContext,
)

if TYPE_CHECKING:
    from operator_use.acp.manager import ACPSessionManager
    from operator_use.auth.acp import ACPAuthManager
    from operator_use.bus.service import Bus
    from operator_use.agent.service import Agent
    from operator_use.acp.types import ACPAgentConfig

logger = logging.getLogger(__name__)

# ── Built-in agents ───────────────────────────────────────────────────────────

_BUILTIN_AGENTS: dict[str, ACPAgentConfig] = {}


# ── Task registry helpers ─────────────────────────────────────────────────────

_acp_task_registries: WeakKeyDictionary = WeakKeyDictionary()


def _task_registry(agent) -> dict:
    if agent is None:
        return {}
    if agent not in _acp_task_registries:
        _acp_task_registries[agent] = {}
    return _acp_task_registries[agent]


def _format_duration(started: datetime, finished: datetime | None) -> str:
    end = finished or datetime.now()
    secs = int((end - started).total_seconds())
    return f'{secs}s' if secs < 60 else f'{secs // 60}m {secs % 60}s'


# ── Schema ────────────────────────────────────────────────────────────────────

class _ACPSchema(BaseModel):
    action: str = Field(
        description=(
            'agents   — list configured agents and their auth/session status.\n'
            'run      — send a task (detached=False to block; detached=True returns immediately).\n'
            'spawn    — create a named session; returns session_id for multi-turn use.\n'
            'send     — send a follow-up into an existing session (requires session_id).\n'
            'sessions — list all persisted ACP session bookmarks.\n'
            'status   — get detailed status of a detached run by task_id.\n'
            'cancel   — stop a running detached task by task_id.'
        )
    )
    agent: Optional[str] = Field(
        default=None,
        description='Agent name (required for run / spawn / send).',
    )
    task: Optional[str] = Field(
        default=None,
        description='Task or message to send (required for run / send; optional for spawn).',
    )
    session_id: Optional[str] = Field(
        default=None,
        description=(
            'Session ID to resume.  Returned by spawn.  '
            'Pass to run/send to continue a prior conversation.'
        ),
    )
    detached: bool = Field(
        default=True,
        description=(
            'If True (default), run in the background; result delivered as a follow-up message. '
            'If False, block until done and return the result directly.'
        ),
    )
    task_id: Optional[str] = Field(
        default=None,
        description='Detached-run task_id — required for status and cancel.',
    )


# ── Tool ──────────────────────────────────────────────────────────────────────

class ACPAgentTool(Tool):
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
                'Interact with remote ACP agents (built-in or configured in settings).\n\n'
                '  agents                                  — list available agents\n'
                '  run, agent=<n>, task=<t>                — dispatch a task\n'
                '  spawn, agent=<n>                        — start a named persistent session\n'
                '  send, agent=<n>, session_id=<id>, task=<t>  — continue a session\n'
                '  sessions                                — view session bookmarks\n'
                '  status, task_id=<id>                    — check a detached run\n'
                '  cancel, task_id=<id>                    — stop a detached run\n\n'
                'detached=True (default): result arrives as a follow-up message.\n'
                'detached=False: blocks and returns the result directly.'
            ),
            schema=_ACPSchema,
            kind=ToolKind.Agent,
            execution_mode=ToolExecutionMode.Sequential,
        )
        self._registry: dict[str, ACPAgentConfig] = {
            **_BUILTIN_AGENTS,
            **{a.name: a for a in registry},
        }
        self._session_manager = session_manager
        self._auth = auth_manager
        self._bus = bus
        self._agent = agent

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback: ToolExecutionUpdateCallback | None = None,
        signal: AbortSignal | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        params = _ACPSchema.model_validate(invocation.params)
        caller_agent = self._agent or (context.agent if context else None)
        bus = self._bus or (context.bus if context else None)

        match params.action:

            # ── agents ────────────────────────────────────────────────────────
            case 'agents':
                return ToolResult.ok(invocation.id, self._list_agents())

            # ── sessions ──────────────────────────────────────────────────────
            case 'sessions':
                return ToolResult.ok(invocation.id, self._list_sessions())

            # ── status ────────────────────────────────────────────────────────
            case 'status':
                if not params.task_id:
                    return ToolResult.error(invocation.id, 'task_id required for status.')
                reg = _task_registry(caller_agent)
                entry = reg.get(params.task_id)
                if not entry:
                    return ToolResult.error(invocation.id, f"No detached run with task_id='{params.task_id}'.")
                r = entry['record']
                dur = _format_duration(r['started_at'], r.get('finished_at'))
                icon = {'running': '⏳', 'done': '✅', 'failed': '❌', 'cancelled': '🚫'}.get(r['status'], '?')
                lines = [
                    f"{icon} {r['task_id']}",
                    f"   agent   : {r['agent']}",
                    f"   status  : {r['status']}",
                    f"   duration: {dur}",
                ]
                if r.get('result'):
                    lines.append(f"\nResult:\n{r['result']}")
                return ToolResult.ok(invocation.id, '\n'.join(lines))

            # ── cancel ────────────────────────────────────────────────────────
            case 'cancel':
                if not params.task_id:
                    return ToolResult.error(invocation.id, 'task_id required for cancel.')
                reg = _task_registry(caller_agent)
                entry = reg.get(params.task_id)
                if not entry:
                    return ToolResult.error(invocation.id, f"No run with task_id='{params.task_id}'.")
                r = entry['record']
                if r['status'] != 'running':
                    return ToolResult.error(invocation.id, f"Run {params.task_id} is not running (status={r['status']}).")
                entry['asyncio_task'].cancel()
                return ToolResult.ok(invocation.id, f"Cancellation requested for task_id={params.task_id}.")

            # ── spawn ─────────────────────────────────────────────────────────
            case 'spawn':
                if not params.agent:
                    return ToolResult.error(invocation.id, "agent is required for action='spawn'.")
                config = self._registry.get(params.agent)
                if config is None:
                    return ToolResult.error(invocation.id, self._unknown_agent(params.agent))
                return await self._spawn(
                    invocation.id, config, params.task, caller_agent, bus
                )

            # ── send ──────────────────────────────────────────────────────────
            case 'send':
                if not params.agent:
                    return ToolResult.error(invocation.id, "agent is required for action='send'.")
                if not params.task:
                    return ToolResult.error(invocation.id, "task is required for action='send'.")
                if not params.session_id:
                    return ToolResult.error(invocation.id, "session_id required for action='send'. Use spawn first.")
                config = self._registry.get(params.agent)
                if config is None:
                    return ToolResult.error(invocation.id, self._unknown_agent(params.agent))
                # send is always blocking — multi-turn needs the response inline.
                return await self._run_blocking(
                    invocation.id, config, params.task, params.session_id, caller_agent
                )

            # ── run ───────────────────────────────────────────────────────────
            case 'run':
                if not params.agent:
                    return ToolResult.error(invocation.id, "agent is required for action='run'.")
                if not params.task:
                    return ToolResult.error(invocation.id, "task is required for action='run'.")
                config = self._registry.get(params.agent)
                if config is None:
                    return ToolResult.error(invocation.id, self._unknown_agent(params.agent))

                # Auto-resume last session if one exists.
                session_id = params.session_id or self._session_manager.get_session_id(params.agent)

                if params.detached:
                    return self._dispatch_detached(
                        invocation.id, config, params.task, session_id, caller_agent, bus
                    )
                return await self._run_blocking(
                    invocation.id, config, params.task, session_id, caller_agent
                )

            case _:
                return ToolResult.error(
                    invocation.id,
                    f"Unknown action '{params.action}'. Use: agents, run, spawn, send, sessions, status, cancel.",
                )

    # ── Blocking run ──────────────────────────────────────────────────────────

    async def _run_blocking(
        self,
        inv_id: str,
        config: ACPAgentConfig,
        task: str,
        session_id: str | None,
        caller_agent: Agent | None,
    ) -> ToolResult:
        try:
            result_text, new_session_id = await self._execute_task(config, task, session_id)
            self._session_manager.save(config.name, new_session_id)
            return ToolResult.ok(inv_id, result_text)
        except Exception as exc:
            logger.exception('ACP blocking run failed | agent=%s', config.name)
            return ToolResult.error(inv_id, f"Agent '{config.name}' failed: {exc}")

    # ── Spawn ─────────────────────────────────────────────────────────────────

    async def _spawn(
        self,
        inv_id: str,
        config: ACPAgentConfig,
        initial_task: str | None,
        caller_agent: Agent | None,
        bus: Bus | None,
    ) -> ToolResult:
        try:
            result_text, session_id = await self._execute_task(
                config, initial_task or '', session_id=None, keep_alive=True
            )
            self._session_manager.save(config.name, session_id)
            body = (
                f"Session '{session_id}' started with agent '{config.name}'.\n\n"
                + (f"{result_text}\n\n" if result_text else "")
                + f"Use action='send', agent='{config.name}', session_id='{session_id}' to continue."
            )
            return ToolResult.ok(inv_id, body)
        except Exception as exc:
            logger.exception('ACP spawn failed | agent=%s', config.name)
            return ToolResult.error(inv_id, f"Agent '{config.name}' spawn failed: {exc}")

    # ── Detached run ──────────────────────────────────────────────────────────

    def _dispatch_detached(
        self,
        inv_id: str,
        config: ACPAgentConfig,
        task: str,
        session_id: str | None,
        caller_agent: Agent | None,
        bus: Bus | None,
    ) -> ToolResult:
        from operator_use.subagent.manager import _session_channel, _session_chat_id
        channel = _session_channel.get()
        chat_id = _session_chat_id.get()
        task_id = f'acp_{config.name}_{uuid.uuid4().hex[:8]}'

        record = {
            'task_id': task_id,
            'agent': config.name,
            'task': task,
            'status': 'running',
            'started_at': datetime.now(),
            'finished_at': None,
            'result': None,
        }
        reg = _task_registry(caller_agent)

        async def _run() -> None:
            try:
                result_text, new_session_id = await self._execute_task(config, task, session_id)
                self._session_manager.save(config.name, new_session_id)
                record['status'] = 'done'
                record['result'] = result_text
            except asyncio.CancelledError:
                record['status'] = 'cancelled'
                record['finished_at'] = datetime.now()
                return
            except Exception as exc:
                logger.exception('ACP detached run failed | agent=%s', config.name)
                result_text = f"(error: {exc})"
                record['status'] = 'failed'
                record['result'] = result_text
            finally:
                record['finished_at'] = datetime.now()

            content = (
                f"Agent: {config.name}\n\n"
                f"Task:\n{task}\n\n"
                f"Response:\n{record['result']}"
            )
            await self._deliver(content, channel, chat_id, caller_agent, bus)

        at = asyncio.create_task(_run(), name=f'acp-agent-{task_id}')
        reg[task_id] = {'record': record, 'asyncio_task': at}

        preview = (task[:120] + '…') if len(task) > 120 else task
        return ToolResult.ok(
            inv_id,
            f"Task dispatched to '{config.name}' (task_id={task_id}).\n"
            f"Task: {preview}\n"
            f"Result will arrive as a follow-up message when done.",
        )

    # ── Core task executor ────────────────────────────────────────────────────

    async def _execute_task(
        self,
        config: ACPAgentConfig,
        task: str,
        session_id: str | None,
        keep_alive: bool = False,
    ) -> tuple[str, str]:
        """Connect, run the task, return (result_text, session_id)."""
        from operator_use.acp.client import ACPClient

        client = self._build_client(config)
        async with client as c:
            async with c.session(resume_id=session_id, keep_alive=keep_alive) as sid:
                result_text = await c.run(task, sid)
                return result_text, sid

    def _build_client(self, config: ACPAgentConfig):
        from operator_use.acp.client import ACPClient

        match config.transport:
            case 'stdio':
                if not config.command:
                    raise ValueError(f"ACP agent '{config.name}' requires a command for stdio transport")
                args = list(config.args)
                return ACPClient.stdio(config.command, *args)
            case 'http':
                if not config.url:
                    raise ValueError(f"ACP agent '{config.name}' requires a url for http transport")
                token = self._auth.get_token(config.name)
                return ACPClient.http(config.url, token=token)
            case 'webrtc':
                if not config.url:
                    raise ValueError(f"ACP agent '{config.name}' requires a room in url for webrtc transport")
                return ACPClient.webrtc(config.url)
            case _:
                raise ValueError(f"Unknown ACP transport {config.transport!r} for agent '{config.name}'")

    # ── Result delivery ───────────────────────────────────────────────────────

    async def _deliver(
        self,
        content: str,
        channel: str | None,
        chat_id: str | None,
        caller_agent: Agent | None,
        bus: Bus | None,
    ) -> None:
        if channel and chat_id and bus is not None:
            from operator_use.bus.types import IncomingMessage, TextPart
            await bus.publish_incoming(IncomingMessage(
                channel=channel,
                chat_id=chat_id,
                parts=[TextPart(content=content)],
                user_id='acp_agent',
                metadata={'source': 'acp_agent'},
            ))
        elif caller_agent is not None:
            from operator_use.message.types import UserMessage
            await caller_agent._engine.follow_up(UserMessage.text(content))

    # ── List helpers ──────────────────────────────────────────────────────────

    def _list_agents(self) -> str:
        if not self._registry:
            return 'No ACP agents configured. Add entries to `acp.agents` in settings.json.'
        lines = ['Registered ACP agents:']
        for name, cfg in self._registry.items():
            has_token = self._auth.has_token(name)
            session = self._session_manager.get(name)
            token_status = 'authenticated' if has_token else 'no credentials'
            session_status = f"session {session['session_id'][:8]}…" if session else 'no session'
            target = cfg.command or cfg.url or 'configured'
            lines.append(f"  {name}  [{cfg.transport}:{target}]  {token_status}  {session_status}")
        return '\n'.join(lines)

    def _list_sessions(self) -> str:
        sessions = self._session_manager.list()
        if not sessions:
            return 'No active ACP sessions.'
        lines = ['ACP session bookmarks:']
        for s in sessions:
            lines.append(
                f"  {s['agent_name']}  session={s['session_id'][:12]}…"
                f"  last_used={s.get('last_used_at', '?')[:19]}"
            )
        return '\n'.join(lines)

    def _unknown_agent(self, name: str) -> str:
        known = ', '.join(self._registry) or '(none configured)'
        return f"Agent '{name}' not in registry. Known: {known}"

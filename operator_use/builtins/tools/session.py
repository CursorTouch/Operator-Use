"""session tool — inspect and message sub-sessions spawned from the main agent.

Actions
-------
list    — list all tracked sub-sessions (ACP and subagent) with status and result preview.
history — show the full result/transcript of a specific session by key.
send    — send a follow-up message to an ACP session by key (blocking).

Session keys come from the <sessions> context block injected at the start of
each turn. They are also returned by acp_agent(action='spawn') and
subagent(action='create').
"""
from __future__ import annotations

import asyncio
from typing import Literal, Optional

from pydantic import BaseModel, Field

from operator_use.tool.types import Tool, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult, ToolContext


class _Schema(BaseModel):
    action: Literal['list', 'history', 'send', 'switch', 'back'] = Field(
        description=(
            'list    — list all tracked sub-sessions with status and result preview.\n'
            'history — show the full result of a specific session (requires session_key).\n'
            'send    — send a follow-up message to an ACP session (requires session_key + message).\n'
            'switch  — push current context onto the stack and enter a session\'s context.\n'
            '          The next turn\'s message history will be built from that session\'s task/result.\n'
            '          (requires session_key)\n'
            'back    — pop the stack and return to the previous context.'
        )
    )
    session_key: Optional[str] = Field(
        default=None,
        description=(
            'Key of the target session — visible in the <sessions> context block as key=...\n'
            'Required for history and send actions.'
        ),
    )
    message: Optional[str] = Field(
        default=None,
        description='Follow-up message or task to deliver. Required for action=send.',
    )


class SessionTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name='session',
            description=(
                'Inspect, message, and switch between sub-sessions spawned from this agent.\n\n'
                '  session(action="list")                              — list all sub-sessions\n'
                '  session(action="history", session_key="<key>")      — full result for a session\n'
                '  session(action="send", session_key="<key>",\n'
                '          message="<follow-up>")                      — continue an ACP session\n'
                '  session(action="switch", session_key="<key>")       — enter a session\'s context\n'
                '  session(action="back")                              — return to previous context\n\n'
                'Session keys appear in the <sessions> context block at the top of each turn. '
                'switch/back change what message history the LLM sees on the next turn.'
            ),
            schema=_Schema,
            kind=ToolKind.Agent,
            execution_mode=ToolExecutionMode.Sequential,
        )

    def get_display_name(self, args: dict) -> str:
        action = args.get('action', '')
        key = args.get('session_key', '') or ''
        if action == 'list': return 'Listing sessions'
        if action == 'history': return f'Session history: {key[:24]}' if key else 'Session history'
        if action == 'send': return f'Send to session: {key[:24]}' if key else 'Session send'
        if action == 'switch': return f'Switch context → {key[:24]}' if key else 'Switch context'
        if action == 'back': return 'Return to previous context'
        return 'Session'

    def is_available(self, context: ToolContext) -> bool:
        return getattr(context, 'session_registry', None) is not None

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        if context is None:
            return ToolResult.error(invocation.id, 'No tool context available.')

        registry = getattr(context, 'session_registry', None)
        if registry is None:
            return ToolResult.error(invocation.id, 'Sub-session registry is not available.')

        params = _Schema.model_validate(invocation.params)

        match params.action:

            case 'list':
                return self._action_list(invocation.id, registry)

            case 'history':
                if not params.session_key:
                    return ToolResult.error(invocation.id, "session_key is required for action='history'.")
                return self._action_history(invocation.id, params.session_key, registry)

            case 'send':
                if not params.session_key:
                    return ToolResult.error(invocation.id, "session_key is required for action='send'.")
                if not params.message:
                    return ToolResult.error(invocation.id, "message is required for action='send'.")
                return await self._action_send(invocation.id, params.session_key, params.message, registry, context, signal)

            case 'switch':
                if not params.session_key:
                    return ToolResult.error(invocation.id, "session_key is required for action='switch'.")
                return self._action_switch(invocation.id, params.session_key, registry, context)

            case 'back':
                return self._action_back(invocation.id, context)

            case _:
                return ToolResult.error(invocation.id, f"Unknown action '{params.action}'. Use: list, history, send, switch, back.")

    # ── list ──────────────────────────────────────────────────────────────────

    def _action_list(self, inv_id: str, registry) -> ToolResult:
        records = registry.list_recent(limit=20)
        if not records:
            return ToolResult.ok(inv_id, 'No sub-sessions have been spawned yet.')

        _icon = {'running': '⏳', 'done': '✅', 'failed': '❌', 'cancelled': '🚫'}
        lines = [f'Sub-sessions ({len(records)} total):', '─' * 60]
        for r in records:
            icon = _icon.get(r.status, '?')
            lines.append(f"{icon} [{r.kind}:{r.agent}]  key={r.key}")
            lines.append(f"   label  : {r.label}")
            lines.append(f"   status : {r.status}")
            lines.append(f"   task   : {r.task[:100]}")
            if r.result:
                preview = r.result[:120].replace('\n', ' ')
                lines.append(f"   result : {preview}{'…' if len(r.result) > 120 else ''}")
            lines.append('')
        return ToolResult.ok(inv_id, '\n'.join(lines).rstrip())

    # ── history ───────────────────────────────────────────────────────────────

    def _action_history(self, inv_id: str, session_key: str, registry) -> ToolResult:
        record = registry.get(session_key)
        if record is None:
            recent = ', '.join(r.key for r in registry.list_recent(limit=5))
            hint = f' Known keys: {recent}' if recent else ''
            return ToolResult.error(inv_id, f"Session key '{session_key}' not found.{hint}")

        _icon = {'running': '⏳', 'done': '✅', 'failed': '❌', 'cancelled': '🚫'}
        lines = [
            f"{_icon.get(record.status, '?')} [{record.kind}:{record.agent}]  key={record.key}",
            f"label   : {record.label}",
            f"status  : {record.status}",
            f"spawned : {record.spawned_at.isoformat(timespec='seconds')}",
        ]
        if record.finished_at:
            lines.append(f"finished: {record.finished_at.isoformat(timespec='seconds')}")
        lines.append(f"\nTask:\n{record.task}")
        if record.result:
            lines.append(f"\nResult:\n{record.result}")
        else:
            lines.append('\n(No result yet — session is still running or produced no output.)')
        return ToolResult.ok(inv_id, '\n'.join(lines))

    # ── switch ────────────────────────────────────────────────────────────────

    def _action_switch(self, inv_id: str, session_key: str, registry, context: ToolContext) -> ToolResult:
        record = registry.get(session_key)
        if record is None:
            recent = ', '.join(r.key for r in registry.list_recent(limit=5))
            hint = f' Known keys: {recent}' if recent else ''
            return ToolResult.error(inv_id, f"Session key '{session_key}' not found.{hint}")

        agent = context.agent
        if agent is None:
            return ToolResult.error(inv_id, 'Agent reference not available for context switch.')

        from operator_use.session.registry import ContextFrame

        # Save current frame onto the stack
        current = agent._active_frame or ContextFrame(kind='main', label='main')
        agent._context_stack.append(current)

        # Activate the target frame
        agent._active_frame = ContextFrame(
            kind='registry',
            label=f'{record.kind}:{record.agent}  ({record.label})',
            registry_key=session_key,
        )

        depth = len(agent._context_stack)
        return ToolResult(
            id=inv_id,
            content=(
                f"Switched to session context '{record.label}' (key={session_key}).\n"
                f"Stack depth: {depth}. Previous context: {current.label}.\n\n"
                f"The next turn's message history will be built from this session's task and result. "
                f"Call session(action='back') to return to '{current.label}'."
            ),
            terminate=True,
        )

    # ── back ──────────────────────────────────────────────────────────────────

    def _action_back(self, inv_id: str, context: ToolContext) -> ToolResult:
        agent = context.agent
        if agent is None:
            return ToolResult.error(inv_id, 'Agent reference not available for context switch.')

        if not agent._context_stack:
            if agent._active_frame is None:
                return ToolResult.error(inv_id, 'Already in main context — nothing to go back to.')
            # Active frame set but stack empty — just clear to main
            agent._active_frame = None
            return ToolResult(
                id=inv_id,
                content='Returned to main context.',
                terminate=True,
            )

        prev = agent._context_stack.pop()
        left = agent._active_frame.label if agent._active_frame else 'main'
        agent._active_frame = None if prev.kind == 'main' else prev

        return ToolResult(
            id=inv_id,
            content=(
                f"Left '{left}', returned to '{prev.label}'.\n"
                f"Stack depth: {len(agent._context_stack)}."
                + (f" Call session(action='back') again to go further back." if agent._context_stack else "")
            ),
            terminate=True,
        )

    # ── send ──────────────────────────────────────────────────────────────────

    async def _action_send(
        self,
        inv_id: str,
        session_key: str,
        message: str,
        registry,
        context: ToolContext,
        signal,
    ) -> ToolResult:
        record = registry.get(session_key)
        if record is None:
            recent = ', '.join(r.key for r in registry.list_recent(limit=5))
            hint = f' Known keys: {recent}' if recent else ''
            return ToolResult.error(inv_id, f"Session key '{session_key}' not found.{hint}")

        if record.kind == 'acp':
            return await self._send_acp(inv_id, record, message, registry, context, signal)

        if record.kind == 'subagent':
            if record.status == 'running':
                return ToolResult.error(
                    inv_id,
                    f"Subagent '{session_key}' is running. Subagents receive their task at spawn time "
                    "and cannot accept follow-up messages mid-run. "
                    "Use subagent(action='status', task_id='...') to check progress.",
                )
            result_preview = (record.result or '(no result)')[:300]
            return ToolResult.ok(
                inv_id,
                f"Subagent '{session_key}' has already completed (status={record.status}).\n\n"
                f"Last result:\n{result_preview}\n\n"
                "To continue this work, spawn a new subagent with the follow-up task.",
            )

        return ToolResult.error(inv_id, f"Unknown session kind '{record.kind}'.")

    async def _send_acp(self, inv_id: str, record, message: str, registry, context: ToolContext, signal) -> ToolResult:
        config = record._acp_config
        if config is None:
            return ToolResult.error(inv_id, f"ACP config not available for session '{record.key}'.")

        from operator_use.acp.client import ACPClient
        from operator_use.subagent.manager import _session_channel, _session_chat_id

        auth = context.acp_auth_manager
        bus = context.bus
        channel = _session_channel.get()
        chat_id = _session_chat_id.get()

        try:
            match config.transport:
                case 'stdio':
                    if not config.command:
                        return ToolResult.error(inv_id, f"ACP agent '{config.name}' requires a command.")
                    client = ACPClient.stdio(config.command, *config.args)
                case 'http':
                    if not config.url:
                        return ToolResult.error(inv_id, f"ACP agent '{config.name}' requires a url.")
                    token = auth.get_token(config.name) if auth else None
                    client = ACPClient.http(config.url, token=token)
                case 'webrtc':
                    if not config.url:
                        return ToolResult.error(inv_id, f"ACP agent '{config.name}' requires a room url.")
                    client = ACPClient.webrtc(config.url)
                case _:
                    return ToolResult.error(inv_id, f"Unknown ACP transport '{config.transport}'.")

            if bus and channel and chat_id:
                client.with_context(bus=bus, channel=channel, chat_id=chat_id)

        except Exception as exc:
            return ToolResult.error(inv_id, f"Failed to build ACP client for '{record.key}': {exc}")

        run_task = asyncio.create_task(_run_acp(client, message, record.key))
        watcher = None
        if signal is not None:
            async def _abort():
                await signal.wait()
                run_task.cancel()
            watcher = asyncio.create_task(_abort())

        try:
            result_text, _ = await run_task
            registry.update(record.key, result=result_text)
            return ToolResult.ok(inv_id, result_text)
        except asyncio.CancelledError:
            return ToolResult.error(inv_id, f"Session '{record.key}' send cancelled.")
        except Exception as exc:
            return ToolResult.error(inv_id, f"Session '{record.key}' send failed: {exc}")
        finally:
            if watcher is not None:
                watcher.cancel()


async def _run_acp(client, message: str, session_id: str) -> tuple[str, str]:
    async with client as c:
        async with c.session(resume_id=session_id) as sid:
            result = await c.run(message, sid)
            return result, sid


tool = SessionTool()

"""peer_agent — delegate tasks to persistent peer agents (other profiles).

Peer agents are long-lived named agents that run in the same Operator process.
Each corresponds to a named profile in ~/.operator/profiles/.  Unlike subagents
(anonymous, ephemeral, no memory), peer agents have their own session history,
tools, skills, and system prompt.

Session bookmarks are stored at:
  profiles/<caller>/peer/<target>.json             ← caller remembers the session_id

Conversation history is stored on BOTH sides (opposite naming):
  profiles/<caller>/peer/sessions/<target>/*.jsonl ← caller's outgoing view
  profiles/<target>/peer/sessions/<caller>/*.jsonl ← target's incoming view

This means every conversation is durable across restarts — the bookmark leads
straight back to the full history, and both agents have their own copy.

Circular delegation (A → B → A) is blocked via a ContextVar that tracks the
current delegation chain for the life of a call stack.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextvars import ContextVar, copy_context
from datetime import datetime
from weakref import WeakKeyDictionary

from pydantic import BaseModel, Field
from typing import Optional

from operator_use.tool.types import Tool, ToolContext, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult
from operator_use.session.manager import SessionManager
from operator_use.session.types import MessageEntry
from operator_use.message.types import AssistantMessage

logger = logging.getLogger(__name__)

# ── Delegation chain (ContextVar — inherited by child tasks) ─────────────────

_chain: ContextVar[tuple[str, ...]] = ContextVar('peer_agent_chain', default=())


def _current_chain() -> tuple[str, ...]:
    return _chain.get()


# ── In-process detached task registry ────────────────────────────────────────
# Keyed by agent instance via WeakKeyDictionary — no attribute monkey-patching,
# naturally GC'd when the agent is destroyed.

_peer_task_registries: WeakKeyDictionary = WeakKeyDictionary()


def _task_registry(context: ToolContext) -> dict:
    agent = context.agent
    if agent is None:
        return {}
    if agent not in _peer_task_registries:
        _peer_task_registries[agent] = {}
    return _peer_task_registries[agent]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _last_response(session_manager: SessionManager) -> str:
    """Extract the text of the last assistant message from a session."""
    for entry in reversed(session_manager.entries):
        if isinstance(entry, MessageEntry) and isinstance(entry.message, AssistantMessage):
            msg = entry.message
            parts = []
            for c in (msg.contents or []):
                if hasattr(c, 'content') and c.content:
                    parts.append(c.content)
            return '\n'.join(parts).strip()
    return ''


def _format_duration(started: datetime, finished: datetime | None) -> str:
    end = finished or datetime.now()
    secs = int((end - started).total_seconds())
    return f'{secs}s' if secs < 60 else f'{secs // 60}m {secs % 60}s'


def _find_session_file(session_dir, session_id: str):
    """Find the .jsonl file in session_dir whose name contains session_id."""
    if not session_dir.exists():
        return None
    for f in session_dir.glob(f'*_{session_id}.jsonl'):
        return f
    return None


def _record_caller_session(
    sessions_dir,
    session_id: str,
    task_text: str,
    response_text: str,
) -> None:
    """Write (or append to) the caller-side session file for this exchange.

    The file mirrors the structure of any other session JSONL:
    a SessionHeader on the first write, then one MessageEntry for the
    outgoing task and one for the incoming response.
    """
    try:
        from operator_use.session.manager import SessionManager
        from operator_use.session.types import SessionOptions
        from operator_use.message.types import UserMessage, AssistantMessage, TextContent

        sessions_dir.mkdir(parents=True, exist_ok=True)
        existing_file = _find_session_file(sessions_dir, session_id)

        sm = SessionManager(
            cwd=sessions_dir,
            session_dir=sessions_dir,
            session_file=existing_file,
            persist=True,
        )
        if existing_file is None:
            sm.new_session(SessionOptions(id=session_id))

        if task_text:
            sm.append_message(UserMessage(contents=[TextContent(content=task_text)]))
        if response_text:
            sm.append_message(AssistantMessage(contents=[TextContent(content=response_text)]))
    except Exception:
        pass  # caller-side recording is best-effort


# ── Schema ────────────────────────────────────────────────────────────────────

class PeerAgentsParams(BaseModel):
    action: str = Field(
        description=(
            'list     — show all available peer profiles and their status.\n'
            'run      — send a task to a peer and wait for its answer.\n'
            'spawn    — create a named persistent session; returns session_id.\n'
            'send     — continue an existing session (requires session_id).\n'
            'sessions — list all active peer sessions opened by this agent.\n'
            'status   — check a detached run by task_id.\n'
            'cancel   — cancel a detached run by task_id.'
        )
    )
    name: Optional[str] = Field(
        default=None,
        description='Target peer profile name (required for run / spawn / send).',
    )
    task: Optional[str] = Field(
        default=None,
        description='Message or task to send to the peer (required for run / send).',
    )
    session_id: Optional[str] = Field(
        default=None,
        description=(
            'Session ID to resume.  Returned by spawn.  '
            'Pass it to send / run to continue a prior conversation.'
        ),
    )
    detached: bool = Field(
        default=False,
        description=(
            'If True, run the peer in the background and return immediately. '
            'The result is injected back into this conversation when done. '
            'End your turn after calling with detached=True.'
        ),
    )
    task_id: Optional[str] = Field(
        default=None,
        description='Detached-run task_id — required for status and cancel.',
    )


# ── Tool ──────────────────────────────────────────────────────────────────────

class PeerAgentsTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name='peer_agent',
            description=(
                'Delegate a task to another profile agent running in this Operator instance.\n\n'
                'Peer agents are persistent, named agents — each has its own session history, '
                'tools, and specialisation.  Conversations survive restarts via session bookmarks.\n\n'
                'Actions:\n'
                '  list     — show available peers.\n'
                '  run      — send a task and wait for the result (or detached=True for background).\n'
                '  spawn    — start a persistent named session; returns a session_id to reuse.\n'
                '  send     — continue a named session with a follow-up message.\n'
                '  sessions — list all peer sessions opened by this agent.\n'
                '  status   — check a detached run.\n'
                '  cancel   — stop a detached run.\n\n'
                'Use subagent for anonymous one-shot parallel work with no persistent state.'
            ),
            schema=PeerAgentsParams,
            kind=ToolKind.Agent,
            execution_mode=ToolExecutionMode.Sequential,
        )

    def get_display_name(self, args: dict) -> str:
        action = args.get('action', '')
        name = args.get('name', '') or ''
        task_id = args.get('task_id', '') or ''
        if action == 'list': return "Listing peers"
        if action == 'run': return f"Running peer: {name}" if name else "Running peer"
        if action == 'spawn': return f"Spawning peer: {name}" if name else "Spawning peer session"
        if action == 'send': return f"Sending to peer: {name}" if name else "Sending to peer"
        if action == 'sessions': return "Listing peer sessions"
        if action == 'status': return f"Peer status: {task_id}" if task_id else "Peer status"
        if action == 'cancel': return f"Cancelling peer: {task_id}" if task_id else "Cancelling peer"
        return "Peer agent"

    def is_available(self, context: ToolContext) -> bool:
        # Only expose this tool when there is at least one peer profile to talk to.
        peers = context.peer_agents or {}
        profiles = {}
        if context.agent is not None and hasattr(context.agent, '_runtime') and context.agent._runtime is not None:
            profiles = context.agent._runtime.get_agent_profiles()
        return bool(peers) or bool(profiles)

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        if context is None:
            return ToolResult.error(id=invocation.id, content='peer_agents: tool context not available.')
        params = PeerAgentsParams(**invocation.params)
        return await _dispatch(params, context, invocation.id)


# ── Dispatch ──────────────────────────────────────────────────────────────────

async def _dispatch(params: PeerAgentsParams, ctx: ToolContext, inv_id: str) -> ToolResult:
    action = params.action

    # ── list ─────────────────────────────────────────────────────────────────
    if action == 'list':
        runtime = _get_runtime(ctx)
        if runtime is None:
            return ToolResult.error(id=inv_id, content='peer_agents: runtime not available.')
        profiles = runtime.get_agent_profiles()
        if not profiles:
            return ToolResult.ok(id=inv_id, content='No peer profiles are configured.')
        peer_agents = ctx.peer_agents or {}
        caller_name = _caller_name(ctx)
        lines = ['Available peer profiles:']
        for name, profile in profiles.items():
            if name == caller_name:
                continue
            built = '● live' if name in peer_agents else '○ idle'
            desc = profile.description or 'No description.'
            lines.append(f'  {built}  {name} — {desc}')
        return ToolResult.ok(id=inv_id, content='\n'.join(lines))

    # ── sessions ─────────────────────────────────────────────────────────────
    if action == 'sessions':
        psm = ctx.peer_session_manager
        if psm is None:
            return ToolResult.ok(id=inv_id, content='No peer sessions (no profile active).')
        bookmarks = psm.list()
        if not bookmarks:
            return ToolResult.ok(id=inv_id, content='No peer sessions started yet.')
        lines = ['Active peer sessions:']
        for b in bookmarks:
            lines.append(f"  {b['profile']}  session_id={b['session_id']}  last_used={b.get('last_used_at', '?')}")
        return ToolResult.ok(id=inv_id, content='\n'.join(lines))

    # ── status ────────────────────────────────────────────────────────────────
    if action == 'status':
        if not params.task_id:
            return ToolResult.error(id=inv_id, content='task_id required for status.')
        reg = _task_registry(ctx)
        entry = reg.get(params.task_id)
        if not entry:
            return ToolResult.error(id=inv_id, content=f"No detached run with task_id='{params.task_id}'.")
        r = entry['record']
        dur = _format_duration(r['started_at'], r.get('finished_at'))
        icon = {'running': '⏳', 'done': '✅', 'failed': '❌', 'cancelled': '🚫'}.get(r['status'], '?')
        lines = [
            f"{icon} {r['task_id']}",
            f"   peer    : {r['name']}",
            f"   status  : {r['status']}",
            f"   duration: {dur}",
        ]
        if r.get('result'):
            lines.append(f"\nResult:\n{r['result']}")
        return ToolResult.ok(id=inv_id, content='\n'.join(lines))

    # ── cancel ────────────────────────────────────────────────────────────────
    if action == 'cancel':
        if not params.task_id:
            return ToolResult.error(id=inv_id, content='task_id required for cancel.')
        reg = _task_registry(ctx)
        entry = reg.get(params.task_id)
        if not entry:
            return ToolResult.error(id=inv_id, content=f"No detached run with task_id='{params.task_id}'.")
        r = entry['record']
        if r['status'] != 'running':
            return ToolResult.error(id=inv_id, content=f"Run {params.task_id} is not running (status={r['status']}).")
        entry['asyncio_task'].cancel()
        return ToolResult.ok(id=inv_id, content=f"Cancellation requested for task_id={params.task_id}.")

    # ── run / spawn / send ────────────────────────────────────────────────────
    if action not in ('run', 'spawn', 'send'):
        return ToolResult.error(id=inv_id, content=f"Unknown action '{action}'. Use: list, run, spawn, send, sessions, status, cancel.")

    if not params.name:
        return ToolResult.error(id=inv_id, content=f"'name' is required for action='{action}'.")
    if action in ('run', 'send') and not params.task:
        return ToolResult.error(id=inv_id, content=f"'task' is required for action='{action}'.")
    if action == 'send' and not params.session_id:
        return ToolResult.error(id=inv_id, content="'session_id' is required for action='send'. Use spawn first.")

    # ── Circular delegation guard ─────────────────────────────────────────────
    caller = _caller_name(ctx)
    chain = _current_chain()
    if params.name in chain or params.name == caller:
        chain_str = ' → '.join([*chain, params.name])
        return ToolResult.error(id=inv_id, content=f'Circular delegation refused: {chain_str}.')

    # ── Resolve / lazily build target agent ───────────────────────────────────
    runtime = _get_runtime(ctx)
    if runtime is None:
        return ToolResult.error(id=inv_id, content='peer_agents: runtime not available.')
    try:
        target_agent = await runtime.get_or_build_peer_agent(params.name)
    except ValueError as exc:
        return ToolResult.error(id=inv_id, content=str(exc))

    # ── Resolve session ───────────────────────────────────────────────────────
    session_id = params.session_id
    psm = ctx.peer_session_manager

    if not session_id and psm is not None:
        # Auto-resume the last session with this peer if one exists.
        session_id = psm.get_session_id(params.name)

    if not session_id:
        # Brand-new session.
        from operator_use.session.utils import create_session_id
        session_id = create_session_id()

    # Point the target agent at its peer/sessions/<caller>/ directory so its
    # conversation is isolated from its own user sessions.
    target_profile = runtime.get_agent_profiles().get(params.name)
    caller_profile = runtime.get_agent_profiles().get(caller) if caller else None
    if target_profile is not None and caller:
        target_sessions_dir = target_profile.peer_sessions_dir(caller)
        target_sessions_dir.mkdir(parents=True, exist_ok=True)
        existing_file = _find_session_file(target_sessions_dir, session_id)
        sm = target_agent._session_manager
        if sm is not None:
            if existing_file:
                sm.set_session(existing_file)
            else:
                from operator_use.session.types import SessionOptions
                sm.session_dir = target_sessions_dir
                sm.new_session(SessionOptions(id=session_id))

    # Mirror the session on the caller's side: peer/sessions/<target>/*.jsonl
    # so both profiles have a copy of the exchange (opposite naming).
    if caller_profile is not None:
        caller_sessions_dir = caller_profile.peer_sessions_dir(params.name)
        caller_sessions_dir.mkdir(parents=True, exist_ok=True)

    task_text = params.task or ''

    if params.detached:
        return await _run_detached(target_agent, params.name, task_text, session_id,
                                   caller, chain, ctx, inv_id)

    # ── Blocking run ──────────────────────────────────────────────────────────
    token = _chain.set((*chain, params.name) if caller else (params.name,))
    try:
        await target_agent.invoke(task_text)
    finally:
        _chain.reset(token)

    result = ''
    if target_agent._session_manager is not None:
        result = _last_response(target_agent._session_manager)

    # Write the exchange into the caller's peer/sessions/<target>/ file.
    if caller_profile is not None:
        _record_caller_session(
            caller_profile.peer_sessions_dir(params.name),
            session_id,
            task_text,
            result,
        )

    # Save bookmark on caller's side.
    if psm is not None:
        psm.save(params.name, session_id)

    if action == 'spawn':
        return ToolResult.ok(
            id=inv_id,
            content=(
                f"Session '{session_id}' started with peer '{params.name}'.\n\n"
                f"{result}\n\n"
                f"Use action='send', name='{params.name}', session_id='{session_id}' to continue."
            ),
        )

    return ToolResult.ok(id=inv_id, content=result or f"(Peer '{params.name}' returned no text.)")


# ── Detached run ──────────────────────────────────────────────────────────────

async def _run_detached(
    target_agent,
    peer_name: str,
    task_text: str,
    session_id: str,
    caller: str,
    chain: tuple[str, ...],
    ctx: ToolContext,
    inv_id: str,
) -> ToolResult:
    task_id = f'peer_{peer_name}_{uuid.uuid4().hex[:8]}'
    record = {
        'task_id': task_id,
        'name': peer_name,
        'task': task_text,
        'status': 'running',
        'started_at': datetime.now(),
        'finished_at': None,
        'result': None,
    }
    reg = _task_registry(ctx)
    new_chain = (*chain, peer_name) if caller else (peer_name,)

    async def _run() -> None:
        token = _chain.set(new_chain)
        try:
            await target_agent.invoke(task_text)
            result = ''
            if target_agent._session_manager is not None:
                result = _last_response(target_agent._session_manager)
            record['status'] = 'done'
            record['result'] = result
        except asyncio.CancelledError:
            record['status'] = 'cancelled'
            return
        except Exception as exc:
            logger.error('peer_agents detached run failed for %r: %s', peer_name, exc, exc_info=True)
            record['status'] = 'failed'
            record['result'] = f'(error: {exc})'
            result = record['result']
        finally:
            _chain.reset(token)
            record['finished_at'] = datetime.now()

        # Save bookmark.
        psm = ctx.peer_session_manager
        if psm is not None:
            psm.save(peer_name, session_id)

        # Inject result back into the calling agent's conversation.
        calling_agent = ctx.agent
        if calling_agent is not None:
            from operator_use.message.types import UserMessage
            preview = (task_text[:100] + '…') if len(task_text) > 100 else task_text
            follow_up_text = (
                f"[peer:{peer_name}] Task complete (task_id={task_id}).\n"
                f"Task: {preview}\n\n"
                f"Result:\n{result}\n\n"
                f"Summarise this result naturally for the user in 1-2 sentences."
            )
            await calling_agent._engine.follow_up(UserMessage.text(follow_up_text))

    ctx_copy = copy_context()
    at = asyncio.create_task(ctx_copy.run(_run), name=f'peer-agent-{task_id}')
    reg[task_id] = {'record': record, 'asyncio_task': at}

    preview = (task_text[:120] + '…') if len(task_text) > 120 else task_text
    return ToolResult.ok(
        id=inv_id,
        content=(
            f"Peer '{peer_name}' is running in the background (task_id={task_id}).\n"
            f"Task: {preview}\n"
            f"Result will be delivered automatically when done.\n"
            f"END YOUR TURN NOW — do not poll or call any other tool."
        ),
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_runtime(ctx: ToolContext):
    agent = ctx.agent
    if agent is None:
        return None
    return getattr(agent, '_runtime', None)


def _caller_name(ctx: ToolContext) -> str:
    agent = ctx.agent
    if agent is None:
        return ''
    profile = agent.get_active_profile() if hasattr(agent, 'get_active_profile') else None
    return profile.name if profile else ''


tool = PeerAgentsTool()

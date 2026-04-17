"""Local agents tool — delegate tasks to persistent peer agents in the Orchestrator.

Design contract
---------------
Local agents are **persistent, named agents** that live for the lifetime of the
Operator process.  They are defined in config (``agents.list``), built at
startup, and held in the Orchestrator's agents registry.  Each one has:

* A dedicated workspace (files, memory, skills)
* Its own session store (per-user conversation history)
* Its own LLM config, tool profile, plugins, and prompt mode
* Its own channels (optional — a local agent may have no public channel)

Delegating to a local agent (this tool) is fundamentally different from
spawning a subagent (``subagents`` tool):

| | Local agent | Subagent |
|---|---|---|
| Lifetime | Process lifetime | Per-task |
| Workspace | Dedicated | None |
| Memory / sessions | Persistent | None |
| Identity | Named, configured | Anonymous |
| Created | At startup from config | On demand by SubagentManager |
| Use when | Delegating to a specialised peer | Fire-and-forget parallel work |

Circular delegation is blocked via a delegation chain passed through message
metadata, so A → B → A raises an error rather than looping forever.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Optional, Literal

from pydantic import BaseModel, Field

from operator_use.bus.views import IncomingMessage, TextPart, OutgoingMessage
from operator_use.messages import HumanMessage
from operator_use.tools import Tool, ToolResult

LOCAL_AGENT_DELEGATION_CHAIN = "_local_agent_delegation_chain"

logger = logging.getLogger(__name__)


class LocalAgents(BaseModel):
    action: Literal["agents", "run", "spawn", "send", "sessions", "status", "cancel"] = Field(
        description=(
            "agents   — list all configured local agents available for delegation. "
            "run      — send a scoped task to another local agent and wait for its final answer. "
            "spawn    — start a persistent named session with an agent (returns session_id). "
            "send     — send a follow-up message into an existing session (alias for run with session_id). "
            "sessions — list all active named sessions tracked by this agent. "
            "status   — get detailed status and result of a specific detached run by task_id. "
            "cancel   — stop a running detached local agent by task_id."
        )
    )
    name: Optional[str] = Field(
        default=None,
        description="Target local agent ID to delegate to (required for run, spawn, send).",
    )
    task: Optional[str] = Field(
        default=None,
        description="Delegated task or message for the target local agent (required for run/send, optional for spawn).",
    )
    session_id: Optional[str] = Field(
        default=None,
        description=(
            "Session ID for multi-turn conversations. "
            "Provide a custom ID with spawn to name the session, or reuse the returned ID in send/run "
            "to continue the conversation with the agent's full prior context."
        ),
    )
    label: Optional[str] = Field(
        default=None,
        description="Human-readable label for a spawned session (used with spawn).",
    )
    detached: bool = Field(
        default=False,
        description=(
            "If True, run the agent in the background and return immediately. "
            "The result will be delivered back to this conversation automatically when done. "
            "END YOUR TURN after calling with detached=True — do not poll or wait. "
            "If False (default), block until the agent finishes and return its result directly."
        ),
    )
    task_id: Optional[str] = Field(
        default=None,
        description="Detached run task_id — required for status and cancel actions.",
    )


def _agent_capabilities(agent) -> str:
    caps: list[str] = []
    if agent.get_plugin("browser_use") is not None:
        caps.append("browser")
    if agent.get_plugin("computer_use") is not None:
        caps.append("computer")
    return ", ".join(caps) if caps else "general"


def _delegation_chain_from_metadata(metadata: dict[str, Any] | None) -> list[str]:
    if not isinstance(metadata, dict):
        return []

    chain = metadata.get(LOCAL_AGENT_DELEGATION_CHAIN, [])
    if not isinstance(chain, list):
        return []

    return [agent_id for agent_id in chain if isinstance(agent_id, str) and agent_id]


def _get_task_registry(agent) -> dict:
    """Lazily initialise a per-agent registry for detached local-agent runs."""
    if not hasattr(agent, "_local_agent_tasks"):
        agent._local_agent_tasks = {}  # task_id -> {"record": dict, "asyncio_task": Task}
    return agent._local_agent_tasks


def _get_session_registry(agent) -> dict:
    """Lazily initialise a per-agent registry for named persistent sessions."""
    if not hasattr(agent, "_local_agent_sessions"):
        agent._local_agent_sessions = {}  # session_id -> {"name": str, "label": str, "created_at": datetime}
    return agent._local_agent_sessions


def _format_duration(started: datetime, finished: datetime | None) -> str:
    end = finished or datetime.now()
    secs = int((end - started).total_seconds())
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m {secs % 60}s"


async def _run_detached(
    target,
    message: HumanMessage,
    session_id: str,
    incoming: IncomingMessage,
    agent_name: str,
    task_id: str,
    bus,
    reply_channel: str,
    reply_chat_id: str,
    reply_account_id: str,
    task_registry: dict,
    pending_replies: dict | None = None,
) -> None:
    """Run a local agent in the background and announce the result via the bus."""
    logger.info(f"[{task_id}] detached local agent '{agent_name}' started")

    # Stream intermediate updates back to the parent channel
    async def publish_intermediate(content: str, is_final: bool, init: bool = False) -> None:
        if not reply_channel or not reply_chat_id or not bus:
            return
        await bus.publish_outgoing(
            OutgoingMessage(
                chat_id=reply_chat_id,
                channel=reply_channel,
                account_id=reply_account_id,
                parts=[TextPart(content=content)],
                reply=False,
                continue_typing=not is_final,
            )
        )

    try:
        response = await target.run(
            message=message,
            session_id=session_id,
            incoming=incoming,
            publish_stream=publish_intermediate if bus else None,
            pending_replies=pending_replies or {},
        )
        result = str(response.content or "")
        status = "completed"
    except asyncio.CancelledError:
        logger.info(f"[{task_id}] detached local agent '{agent_name}' cancelled")
        if task_id in task_registry:
            task_registry[task_id]["record"]["status"] = "cancelled"
            task_registry[task_id]["record"]["finished_at"] = datetime.now()
        return
    except Exception as e:
        logger.error(f"[{task_id}] detached local agent '{agent_name}' failed: {e}", exc_info=True)
        result = f"(error: {type(e).__name__}: {e})"
        status = "failed"

    if task_id in task_registry:
        task_registry[task_id]["record"]["status"] = status
        task_registry[task_id]["record"]["finished_at"] = datetime.now()
        task_registry[task_id]["record"]["result"] = result

    logger.info(f"[{task_id}] detached local agent '{agent_name}' done — status={status}")

    content = (
        f"[localagent:{task_id}] Agent '{agent_name}' has finished.\n\n"
        f"Result:\n{result}\n\n"
        f"Relay this result naturally in context. Do not mention task IDs or 'localagent'."
    )
    await bus.publish_incoming(
        IncomingMessage(
            channel=reply_channel,
            chat_id=reply_chat_id,
            account_id=reply_account_id,
            parts=[TextPart(content=content)],
            user_id="localagent",
            metadata={"_localagent_result": True, "task_id": task_id, "from_agent": agent_name},
        )
    )


@Tool(
    name="localagents",
    description=(
        "Delegate a task to a persistent peer agent running in this Operator instance.\n\n"
        "Local agents are long-lived, named agents defined in config — each has its own "
        "workspace, memory, tool profile, and specialisation. Use this tool when you want "
        "to hand off work to a specific named peer (e.g. a 'research' or 'coding' agent) "
        "rather than spinning up an anonymous background worker.\n\n"
        "Actions:\n"
        "  agents   — list all available local agents and their capabilities.\n"
        "  run      — send a scoped task to a named peer and wait for its result.\n"
        "             Set detached=True to run in the background; result is delivered automatically.\n"
        "  spawn    — start a persistent named session with an agent; returns a session_id.\n"
        "             Reuse that session_id in run/send to continue the multi-turn conversation.\n"
        "  send     — send a follow-up message into an existing session (requires name + session_id).\n"
        "  sessions — list all active named sessions tracked by this agent.\n"
        "  status   — get detailed status and full result of a specific detached run by task_id.\n"
        "  cancel   — stop a running detached local agent by task_id.\n\n"
        "Use the 'subagents' tool instead when you need anonymous parallel workers with no "
        "persistent state."
    ),
    model=LocalAgents,
)
async def localagents(
    action: str,
    name: str | None = None,
    task: str | None = None,
    session_id: str | None = None,
    label: str | None = None,
    detached: bool = False,
    task_id: str | None = None,
    **kwargs,
) -> ToolResult:
    registry: dict = kwargs.get("_agent_registry") or {}
    current_agent = kwargs.get("_agent")
    current_agent_id = kwargs.get("_agent_id", "")
    current_metadata = kwargs.get("_metadata") or {}
    parent_session_id = kwargs.get("_session_id", "delegation")
    parent_channel = kwargs.get("_channel") or "direct"
    parent_chat_id = kwargs.get("_chat_id") or parent_session_id
    parent_account_id = kwargs.get("_account_id") or current_agent_id

    if action == "agents":
        if not registry:
            return ToolResult.success_result("No local agents are configured.")

        lines = ["Available local agents:"]
        for agent_id, agent in registry.items():
            marker = " (current)" if agent_id == current_agent_id else ""
            description = getattr(agent, "description", "") or "No description provided."
            lines.append(
                f"  • {agent_id}{marker} — {description} "
                f"[capabilities: {_agent_capabilities(agent)}]"
            )

        # Show any active detached runs from this agent
        if current_agent is not None:
            task_reg = _get_task_registry(current_agent)
            if task_reg:
                running = [
                    e["record"] for e in task_reg.values() if e["record"]["status"] == "running"
                ]
                if running:
                    lines.append(f"\nActive detached runs ({len(running)} running):")
                    for r in running:
                        dur = _format_duration(r["started_at"], None)
                        lines.append(f"  ⏳ {r['task_id']}  [{r['name']}]  {dur}")

        return ToolResult.success_result("\n".join(lines))

    if action == "status":
        if not task_id:
            return ToolResult.error_result("Provide task_id to check status")
        if current_agent is None:
            return ToolResult.error_result("Agent context not available (internal error)")
        task_reg = _get_task_registry(current_agent)
        entry = task_reg.get(task_id)
        if not entry:
            return ToolResult.error_result(f"No detached run found with task_id='{task_id}'")
        r = entry["record"]
        dur = _format_duration(r["started_at"], r.get("finished_at"))
        status_icon = {
            "running": "⏳",
            "completed": "✅",
            "failed": "❌",
            "cancelled": "🚫",
        }.get(r["status"], "?")
        lines = [
            f"{status_icon} task_id : {r['task_id']}",
            f"   agent   : {r['name']}",
            f"   status  : {r['status']}",
            f"   duration: {dur}",
            f"   started : {r['started_at'].isoformat(timespec='seconds')}",
        ]
        if r.get("finished_at"):
            lines.append(f"   finished: {r['finished_at'].isoformat(timespec='seconds')}")
        lines.append(f"\nTask:\n{r['task']}")
        if r.get("result"):
            lines.append(f"\nResult:\n{r['result']}")
        return ToolResult.success_result("\n".join(lines))

    if action == "cancel":
        if not task_id:
            return ToolResult.error_result("Provide task_id to cancel")
        if current_agent is None:
            return ToolResult.error_result("Agent context not available (internal error)")
        task_reg = _get_task_registry(current_agent)
        entry = task_reg.get(task_id)
        if not entry:
            return ToolResult.error_result(f"No detached run found with task_id='{task_id}'")
        r = entry["record"]
        if r["status"] != "running":
            return ToolResult.error_result(
                f"Cannot cancel — agent '{r['name']}' run {task_id} is not running (status={r['status']})"
            )
        entry["asyncio_task"].cancel()
        return ToolResult.success_result(
            f"Cancellation requested for agent '{r['name']}' (task_id={task_id})."
        )

    if action == "sessions":
        if current_agent is None:
            return ToolResult.error_result("Agent context not available (internal error)")
        session_reg = _get_session_registry(current_agent)
        if not session_reg:
            return ToolResult.success_result("No named sessions have been spawned yet.")
        lines = ["Active named sessions:"]
        for sid, info in session_reg.items():
            age = _format_duration(info["created_at"], None)
            lines.append(f"  • {sid}  agent='{info['name']}'  label='{info['label']}'  age={age}")
        return ToolResult.success_result("\n".join(lines))

    if action in ("spawn", "send"):
        if not name:
            return ToolResult.error_result(f"name is required for action='{action}'")
        if action == "send" and not task:
            return ToolResult.error_result("task is required for action='send'")
        if action == "send" and not session_id:
            return ToolResult.error_result(
                "session_id is required for action='send' — use spawn first to create a session"
            )

    if action != "run" and action not in ("spawn", "send"):
        return ToolResult.error_result(f"Unknown action '{action}'")

    if not name:
        return ToolResult.error_result("name is required for action='run'")
    if not task and action != "spawn":
        return ToolResult.error_result("task is required for action='run'")

    target = registry.get(name)
    if target is None:
        available = ", ".join(registry.keys()) if registry else "none"
        return ToolResult.error_result(f"Unknown local agent '{name}'. Available: {available}.")

    if current_agent is not None and target is current_agent:
        return ToolResult.error_result(
            "Refusing to delegate to the current agent. Choose a different local agent."
        )

    delegation_chain = _delegation_chain_from_metadata(current_metadata)
    if current_agent_id and (not delegation_chain or delegation_chain[-1] != current_agent_id):
        delegation_chain = [*delegation_chain, current_agent_id]
    if name in delegation_chain:
        chain_text = " -> ".join([*delegation_chain, name])
        return ToolResult.error_result(
            f"Refusing circular local delegation: {chain_text}. Choose a target outside the current delegation chain."
        )

    if session_id:
        delegated_session_id = session_id
    elif action == "spawn":
        delegated_session_id = f"spawned_{name}_{uuid.uuid4().hex[:8]}"
    else:
        delegated_session_id = (
            f"{parent_session_id}__delegate__{current_agent_id or 'agent'}-to-{name}"
        )

    # Register the session for spawn
    if action == "spawn" and current_agent is not None:
        session_reg = _get_session_registry(current_agent)
        session_reg[delegated_session_id] = {
            "name": name,
            "label": label or delegated_session_id,
            "created_at": datetime.now(),
        }

    # Create a shared pending_replies dict for parent-child communication
    # Child can ask parent, parent can respond to child
    child_pending_replies: dict = {}

    delegated_metadata = {
        **current_metadata,
        "_delegated_local_agent_call": True,
        "from_agent": current_agent_id,
        "to_agent": name,
        "_parent_session_id": parent_session_id,
        "_parent_pending_replies": child_pending_replies,
        LOCAL_AGENT_DELEGATION_CHAIN: [*delegation_chain, name],
    }
    incoming = IncomingMessage(
        channel=parent_channel,
        chat_id=parent_chat_id,
        account_id=name,
        user_id=current_agent_id or "agent",
        parts=[TextPart(content=task)],
        metadata=delegated_metadata,
    )
    message = HumanMessage(content=task, metadata=incoming.metadata)

    if detached:
        bus = kwargs.get("_bus")
        if bus is None:
            return ToolResult.error_result("Bus not available for detached mode (internal error).")

        run_task_id = f"local_{name}_{uuid.uuid4().hex[:8]}"

        task_reg = _get_task_registry(current_agent) if current_agent is not None else {}
        record = {
            "task_id": run_task_id,
            "name": name,
            "task": task,
            "status": "running",
            "started_at": datetime.now(),
            "finished_at": None,
            "result": None,
        }

        at = asyncio.create_task(
            _run_detached(
                target=target,
                message=message,
                session_id=delegated_session_id,
                incoming=incoming,
                agent_name=name,
                task_id=run_task_id,
                bus=bus,
                reply_channel=parent_channel,
                reply_chat_id=parent_chat_id,
                reply_account_id=parent_account_id,
                task_registry=task_reg,
                pending_replies=child_pending_replies,
            ),
            name=f"localagent-{run_task_id}",
        )
        task_reg[run_task_id] = {"record": record, "asyncio_task": at}

        return ToolResult.success_result(
            f"Agent '{name}' started in background (task_id={run_task_id}).\n"
            f"Result will be delivered automatically when done.\n"
            f"END YOUR TURN. Inform the user and stop."
        )

    if action == "spawn" and not task:
        return ToolResult.success_result(
            f"Session '{delegated_session_id}' created for agent '{name}'.\n"
            f"Use action='send' with session_id='{delegated_session_id}' to start the conversation."
        )

    # Create a publish_stream callback so intermediate messages from the subagent
    # flow back to the parent's channel in real-time
    async def publish_intermediate(content: str, is_final: bool, init: bool = False) -> None:
        if not parent_channel or not parent_chat_id or not bus:
            return
        # Publish intermediate messages to the parent channel while the subagent runs
        await bus.publish_outgoing(
            OutgoingMessage(
                chat_id=parent_chat_id,
                channel=parent_channel,
                account_id=parent_account_id,
                parts=[TextPart(content=content)],
                reply=False,
                continue_typing=not is_final,
            )
        )

    bus = kwargs.get("_bus")

    # Pass the shared pending_replies dict so child can register requests and parent can respond
    response = await target.run(
        message=message,
        session_id=delegated_session_id,
        incoming=incoming,
        publish_stream=publish_intermediate if bus else None,
        pending_replies=child_pending_replies,  # ← Child's requests will be in this dict
    )

    # After child finishes, check if there were any pending requests that parent didn't respond to
    # and log them so parent can review
    unresolved = [
        v for v in child_pending_replies.values() if isinstance(v, dict) and "future" in v
    ]
    if unresolved:
        logger.warning(
            f"Child agent '{name}' left {len(unresolved)} unresolved parent requests. "
            f"Parent did not respond to: {[v['content'][:50] for v in unresolved]}"
        )

    if action == "spawn":
        return ToolResult.success_result(
            f"Session '{delegated_session_id}' created for agent '{name}'.\n\n"
            f"{response.content or ''}\n\n"
            f"Continue with action='send', name='{name}', session_id='{delegated_session_id}'."
        )

    return ToolResult.success_result(str(response.content or ""))

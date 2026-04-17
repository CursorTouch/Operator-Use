"""ACP Agents tool — call pre-approved remote ACP agents from within the Operator agent.

The LLM never supplies connection details (base_url, auth_token). Those are
defined by the user in config under `acp_agents` and resolved here by name.

Actions:
  - agents   : list all pre-configured ACP agents available to call
  - run      : send a task to a named agent and get the full response
  - stream   : send a task and receive streamed output chunks
  - sessions : list active local sessions tracked by this agent
  - status   : poll the status/output of an existing run by run_id
  - cancel   : cancel a running task by run_id

Usage by the LLM:
    acpagents(action="agents")
    acpagents(action="run", name="claude-code", input="explain auth.py")
    acpagents(action="run", name="claude-code", input="continue", session_id="s1")
    acpagents(action="stream", name="gemini", input="summarise logs")
"""

from __future__ import annotations
from datetime import datetime
import json
from pathlib import Path
from typing import Literal, Optional
import uuid

from pydantic import BaseModel, Field

from operator_use.acp.client import ACPClient
from operator_use.acp.config import ACPClientConfig
from operator_use.acp.models import TextMessagePart
from operator_use.tools import Tool, ToolResult
from operator_use.config.paths import get_userdata_dir


class ACPAgents(BaseModel):
    action: Literal["agents", "run", "spawn", "send", "stream", "sessions", "status", "cancel"] = (
        Field(
            description=(
                "agents   — list all pre-configured ACP agents available to call. "
                "run      — send a task to the named agent and wait for the full response. "
                "spawn    — start a persistent session (generates a session_id if missing). "
                "send     — send a message to an existing session (alias for run). "
                "stream   — send a task and receive response as streamed text (chunks joined). "
                "sessions — list active local sessions tracked by this agent. "
                "status   — get the current status and output of an existing run by run_id. "
                "cancel   — cancel a running task by run_id."
            )
        )
    )
    name: Optional[str] = Field(
        default=None,
        description=(
            "Name of the pre-configured ACP agent to call (e.g. 'claude-code', 'gemini'). "
            "Use action='agents' to see available names. Required for most actions."
        ),
    )
    input: Optional[str] = Field(
        default=None,
        description="Task or message to send to the remote agent (required for run/spawn/send/stream).",
    )
    session_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional session ID for multi-turn conversations. "
            "Reuse the same session_id across calls to maintain conversation history with the remote agent."
        ),
    )
    label: Optional[str] = Field(
        default=None,
        description="Human-readable label for a session (used with spawn).",
    )
    run_id: Optional[str] = Field(
        default=None,
        description="Run ID returned by a previous run/stream call (required for status/cancel).",
    )
    timeout: Optional[float] = Field(
        default=None,
        description="Override the default timeout in seconds for this call.",
    )


def _load_sessions(userdata_dir: Path) -> dict:
    path = userdata_dir / "acp_sessions.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_sessions(userdata_dir: Path, sessions: dict) -> None:
    path = userdata_dir / "acp_sessions.json"
    path.write_text(json.dumps(sessions, indent=4), encoding="utf-8")


def _resolve_entry(name: str, registry: dict):
    """Return the ACPAgentEntry for `name`, or None if not found."""
    return registry.get(name)


def _make_client_config(entry, timeout_override: float | None = None) -> ACPClientConfig:
    return ACPClientConfig(
        enabled=True,
        base_url=entry.base_url,
        agent_id=entry.agent_id or "operator",
        auth_token=entry.auth_token,
        timeout=timeout_override if timeout_override is not None else entry.timeout,
    )


@Tool(
    name="acpagents",
    description=(
        "Call pre-approved remote ACP-compatible agents (e.g. Claude Code, Gemini CLI, other Operator instances). "
        "Available agents are defined by the user in config — use action='agents' to list them. "
        "Supports persistent sessions, streaming, and run management."
    ),
    model=ACPAgents,
)
async def acpagents(
    action: str,
    name: str | None = None,
    input: str | None = None,
    session_id: str | None = None,
    label: str | None = None,
    run_id: str | None = None,
    timeout: float | None = None,
    **kwargs,
) -> ToolResult:
    userdata_dir = kwargs.get("_userdata_dir") or get_userdata_dir()
    registry: dict = kwargs.get("_acp_registry") or {}

    # ── agents: show the pre-configured registry ──────────────────────────────
    if action == "agents":
        if not registry:
            return ToolResult.success_result(
                "No ACP agents configured. Ask the user to add entries under 'acp_agents' in config.json."
            )
        lines = ["Available ACP agents (configured by user):"]
        for agent_name, entry in registry.items():
            desc = f" — {entry.description}" if entry.description else ""
            lines.append(f"  • {agent_name}{desc}")
        return ToolResult.success_result("\n".join(lines))

    # ── sessions: show local session tracking ─────────────────────────────────
    if action == "sessions":
        sessions = _load_sessions(userdata_dir)
        if not sessions:
            return ToolResult.success_result("No active ACP sessions tracked.")
        lines = ["Active ACP Sessions:"]
        for sid, info in sessions.items():
            lines.append(f"  • {sid} ({info.get('label') or 'no label'})")
            lines.append(f"    agent: {info.get('name')} @ {info.get('base_url')}")
        return ToolResult.success_result("\n".join(lines))

    # ── all other actions require a valid name from the registry ──────────────
    if not name:
        return ToolResult.error_result(
            "name is required. Use action='agents' to see available agent names."
        )

    entry = _resolve_entry(name, registry)
    if entry is None:
        available = ", ".join(registry.keys()) if registry else "none"
        return ToolResult.error_result(
            f"Unknown agent '{name}'. Available: {available}. "
            "Ask the user to add it under 'acp_agents' in config.json."
        )

    cfg = _make_client_config(entry, timeout_override=timeout)

    match action:
        case "run" | "spawn" | "send":
            if not input:
                return ToolResult.error_result(f"input is required for action='{action}'")

            if action == "spawn" and not session_id:
                session_id = f"acp_{uuid.uuid4().hex[:8]}"

            # Auto-discover remote agent_id if not set in registry entry
            if not cfg.agent_id or cfg.agent_id == "operator":
                try:
                    async with ACPClient(cfg) as client:
                        resp = await client.list_agents()
                    if resp.agents:
                        cfg.agent_id = resp.agents[0].id
                except Exception:
                    pass

            try:
                async with ACPClient(cfg) as client:
                    result = await client.run(input, session_id=session_id)

                if session_id:
                    sessions = _load_sessions(userdata_dir)
                    sessions[session_id] = {
                        "name": name,
                        "base_url": entry.base_url,
                        "agent_id": cfg.agent_id,
                        "label": label or session_id,
                        "last_updated": datetime.now().isoformat(),
                    }
                    _save_sessions(userdata_dir, sessions)

                return ToolResult.success_result(
                    f"Session: {session_id}\n\n{result}" if session_id else result
                )
            except Exception as e:
                return ToolResult.error_result(f"ACP {action} failed: {e}")

        case "stream":
            if not input:
                return ToolResult.error_result("input is required for action='stream'")
            if not cfg.agent_id or cfg.agent_id == "operator":
                try:
                    async with ACPClient(cfg) as client:
                        resp = await client.list_agents()
                    if resp.agents:
                        cfg.agent_id = resp.agents[0].id
                except Exception:
                    pass
            try:
                chunks: list[str] = []
                async with ACPClient(cfg) as client:
                    async for chunk in client.run_stream(input, session_id=session_id):
                        chunks.append(chunk)
                return ToolResult.success_result("".join(chunks))
            except Exception as e:
                return ToolResult.error_result(f"ACP stream failed: {e}")

        case "status":
            if not run_id:
                return ToolResult.error_result("run_id is required for action='status'")
            try:
                async with ACPClient(cfg) as client:
                    run = await client.get_run(run_id)
                lines = [
                    f"run_id : {run.id}",
                    f"status : {run.status.value}",
                    f"agent  : {run.agent_id}",
                ]
                if run.session_id:
                    lines.append(f"session: {run.session_id}")
                if run.finished_at:
                    lines.append(f"finished: {run.finished_at.isoformat()}")
                if run.error:
                    lines.append(f"error  : {run.error}")
                if run.output:
                    text = "".join(p.text for p in run.output if isinstance(p, TextMessagePart))
                    if text:
                        lines.append(f"\nOutput:\n{text}")
                return ToolResult.success_result("\n".join(lines))
            except Exception as e:
                return ToolResult.error_result(f"Failed to get status: {e}")

        case "cancel":
            if not run_id:
                return ToolResult.error_result("run_id is required for action='cancel'")
            try:
                async with ACPClient(cfg) as client:
                    await client.cancel_run(run_id)
                return ToolResult.success_result(f"Run {run_id} cancelled.")
            except Exception as e:
                return ToolResult.error_result(f"Failed to cancel run {run_id}: {e}")

        case _:
            return ToolResult.error_result(f"Unknown action '{action}'")

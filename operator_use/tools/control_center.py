"""Control Center tool: restart Operator.

Security note
-------------
This tool gives the LLM direct control over process-level behaviour (restart).
In production deployments it should only be reachable from trusted channels.
All calls are audit-logged at WARNING level so they appear in operator.log
regardless of the configured log level.
"""

import asyncio
import json
import logging
import os
import sys
from typing import Optional

from pydantic import BaseModel, Field

from operator_use.config.paths import get_userdata_dir
from operator_use.tools import Tool, ToolResult

logger = logging.getLogger(__name__)

RESTART_FILE = get_userdata_dir() / "restart.json"

# Set to 75 when a graceful restart is requested so the worker exits with the
# right code after asyncio cleanup finishes (rather than calling os._exit).
_requested_exit_code: int = 0


def requested_exit_code() -> int:
    """Return 75 if a restart was requested via control_center, else 0."""
    return _requested_exit_code


def request_restart() -> None:
    """Mark exit code 75 (restart) without running the countdown animation."""
    global _requested_exit_code
    _requested_exit_code = 75


class ControlCenter(BaseModel):
    restart: bool = Field(
        default=False,
        description=(
            "Restart Operator to reload config or pick up code changes. "
            "Use alone to restart without changing any settings."
        ),
    )
    continue_with: Optional[str] = Field(
        default=None,
        description=(
            "Set when restart=true and there is more work to do after rebooting. "
            "Describe exactly what to continue — e.g. 'Test the new tool I just added'. "
            "Omit when restart is the final action."
        ),
    )


async def _do_restart(graceful_fn=None) -> None:
    """Animate a restart countdown, then shut down gracefully or force-exit."""
    global _requested_exit_code
    os.system("cls" if os.name == "nt" else "clear")
    frames = ["↑", "↗", "→", "↘", "↓", "↙", "←", "↖"]
    for i in range(20):
        sys.stdout.write(f"\r {frames[i % len(frames)]}  Restarting Operator...")
        sys.stdout.flush()
        await asyncio.sleep(0.5)
    sys.stdout.write("\n")
    sys.stdout.flush()
    _requested_exit_code = 75
    if graceful_fn is not None:
        await graceful_fn()
    else:
        os._exit(75)  # fallback: skips cleanup, but guarantees termination


@Tool(
    name="control_center",
    description=(
        "Restart Operator to reload config or pick up code changes. "
        "Set continue_with to describe what to do after the restart."
    ),
    model=ControlCenter,
)
async def control_center(
    restart: bool = False,
    continue_with: Optional[str] = None,
    **kwargs,
) -> ToolResult:
    caller_channel = kwargs.get("_channel", "unknown")
    caller_chat_id = kwargs.get("_chat_id", "unknown")
    caller_agent_id = kwargs.get("_agent_id", "unknown")

    logger.warning(
        "control_center called | agent=%s channel=%s chat=%s | restart=%s",
        caller_agent_id,
        caller_channel,
        caller_chat_id,
        restart,
    )

    if not restart:
        return ToolResult.success_result("No action requested. Pass restart=true to restart.")

    if continue_with:
        channel = kwargs.get("_channel")
        chat_id = kwargs.get("_chat_id")
        account_id = kwargs.get("_account_id", "")
        interceptor = kwargs.get("_interceptor")
        improvement_session = interceptor.session_id if interceptor else None
        if improvement_session and interceptor:
            try:
                interceptor.generate_diffs()
            except Exception:
                pass
        run_id = kwargs.get("_run_id")
        restart_data: dict = {
            "resume_task": continue_with,
            "channel": channel,
            "chat_id": chat_id,
            "account_id": account_id,
        }
        if improvement_session:
            restart_data["improvement_session"] = improvement_session
        if run_id:
            restart_data["run_id"] = run_id
            try:
                if RESTART_FILE.exists():
                    _prev = json.loads(RESTART_FILE.read_text(encoding="utf-8"))
                    _orig = _prev.get("deferred_task")
                    if _orig:
                        restart_data["deferred_task"] = _orig
            except Exception:
                pass
        try:
            RESTART_FILE.parent.mkdir(parents=True, exist_ok=True)
            RESTART_FILE.write_text(json.dumps(restart_data), encoding="utf-8")
        except Exception as e:
            return ToolResult.error_result(f"Could not save restart continuation: {e}")

    graceful_fn = kwargs.get("_graceful_restart_fn")
    gateway = kwargs.get("_gateway")
    on_restart = getattr(gateway, "on_restart", None)
    if callable(on_restart):
        asyncio.ensure_future(on_restart())
    else:
        asyncio.ensure_future(_do_restart(graceful_fn=graceful_fn))

    msg = "Restart initiated."
    if continue_with:
        msg += f" Will continue: {continue_with[:100]}"
    return ToolResult.success_result(msg, metadata={"stop_loop": True})

"""Control Center tool: toggle plugins and restart.

Security note
-------------
This tool gives the LLM direct control over process-level behaviour (plugin
toggle, process restart).  In production deployments it should only be
reachable from trusted channels.  All calls are audit-logged at WARNING level
so they appear in the operator.log regardless of the configured log level.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from typing import Optional

from pydantic import BaseModel, Field

from operator_use.config.paths import get_userdata_dir
from operator_use.tools import Tool, ToolResult

logger = logging.getLogger(__name__)

CONFIG_PATH = get_userdata_dir() / "config.json"
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
    computer_use: Optional[bool] = Field(
        default=None,
        description="Enable or disable computer_use (Windows GUI automation).",
    )
    browser_use: Optional[bool] = Field(
        default=None,
        description="Enable or disable browser_use (Chrome DevTools automation).",
    )
    restart: bool = Field(
        default=False,
        description=(
            "Restart Operator after applying changes to reload the config. "
            "Also use this alone (no other args) to restart without changing any settings."
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
    agent_id: Optional[str] = Field(
        default=None,
        description="Target agent ID. Defaults to the first agent in config.",
    )


def _load_config_raw() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_config_raw(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _get_agent_entry(data: dict, agent_id: Optional[str]) -> tuple[dict, int] | tuple[None, None]:
    agents_list: list = data.get("agents", {}).get("list", [])
    if not agents_list:
        return None, None
    if agent_id is None:
        return agents_list[0], 0
    for i, entry in enumerate(agents_list):
        if entry.get("id") == agent_id:
            return entry, i
    return None, None


def _set_plugin_enabled(entry: dict, plugin_id: str, enabled: bool) -> None:
    """Update or insert a plugin entry in the agent's plugins list."""
    plugins: list = entry.setdefault("plugins", [])
    for p in plugins:
        if p.get("id") == plugin_id:
            p["enabled"] = enabled
            return
    plugins.append({"id": plugin_id, "enabled": enabled})


def _get_plugin_enabled(entry: dict, plugin_id: str) -> bool:
    for p in entry.get("plugins", []):
        if p.get("id") == plugin_id:
            return bool(p.get("enabled", True))
    return False


async def _do_restart(graceful_fn=None) -> None:
    """Animate a restart countdown, then shut down gracefully or force-exit.

    If *graceful_fn* is provided (injected by start.py via ``_graceful_restart_fn``
    tool extension) it is awaited — this cancels the running asyncio tasks, lets
    the ``finally`` block in ``main()`` run (gateway.stop, cron.stop, etc.), and
    then the worker exits via ``sys.exit(requested_exit_code())``.

    If *graceful_fn* is None (e.g. in tests or when not wired up), falls back to
    ``os._exit(75)`` which skips cleanup but guarantees the process terminates.
    """
    global _requested_exit_code
    subprocess.run("cls" if os.name == "nt" else "clear", shell=True, check=False)
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
        "Control Center for Operator capabilities.\n\n"
        "Dynamically toggle computer_use (GUI automation) and browser_use (Browser automation via CDP). "
        "Both can be enabled or disabled independently. "
        "No restart needed for capability toggles.\n\n"
        "- computer_use=true  → enable desktop automation tools + desktop state in context\n"
        "- browser_use=true   → enable browser tools + browser state in context\n"
        "- computer_use=false / browser_use=false → disable and remove from context\n"
        "- restart=true       → restart Operator (use for code/config changes)\n"
        "- Call with no arguments to get current status."
    ),
    model=ControlCenter,
)
async def control_center(
    computer_use: Optional[bool] = None,
    browser_use: Optional[bool] = None,
    restart: bool = False,
    continue_with: Optional[str] = None,
    agent_id: Optional[str] = None,
    **kwargs,
) -> ToolResult:
    caller_channel = kwargs.get("_channel", "unknown")
    caller_chat_id = kwargs.get("_chat_id", "unknown")
    caller_agent_id = kwargs.get("_agent_id", "unknown")

    data = _load_config_raw()
    agents_block = data.setdefault("agents", {})
    agents_list: list = agents_block.setdefault("list", [])

    entry, idx = _get_agent_entry(data, agent_id)
    if entry is None:
        return ToolResult.error_result(
            "No agents found in config.json. Run 'operator onboard' first."
        )

    agent = kwargs.get("_agent")

    changes = []
    if computer_use is not None:
        _set_plugin_enabled(entry, "computer_use", computer_use)
        if computer_use:
            changes.append("computer_use=true")
            if agent is not None:
                await agent.enable_computer_use()
        else:
            changes.append("computer_use=false")
            if agent is not None:
                await agent.disable_computer_use()

    if browser_use is not None:
        _set_plugin_enabled(entry, "browser_use", browser_use)
        if browser_use:
            changes.append("browser_use=true")
            if agent is not None:
                await agent.enable_browser_use()
        else:
            changes.append("browser_use=false")
            if agent is not None:
                await agent.disable_browser_use()

    agents_list[idx] = entry
    _save_config_raw(data)

    cu = _get_plugin_enabled(entry, "computer_use")
    bu = _get_plugin_enabled(entry, "browser_use")
    status = f"Agent: {entry.get('id', '?')}\n  computer_use : {cu}\n  browser_use  : {bu}"

    if changes:
        msg = f"Updated — {', '.join(changes)}.\n{status}"
    else:
        msg = status

    # Audit log — always emitted at WARNING so it appears in operator.log
    logger.warning(
        "control_center called | agent=%s channel=%s chat=%s | changes=[%s] restart=%s",
        caller_agent_id,
        caller_channel,
        caller_chat_id,
        ", ".join(changes) if changes else "none",
        restart,
    )

    if restart:
        if continue_with:
            channel = kwargs.get("_channel")
            chat_id = kwargs.get("_chat_id")
            account_id = kwargs.get("_account_id", "")
            # Grab the active code-change session ID (if the agent edited any
            # .py files this cycle) so the supervisor can revert those files
            # if the new worker fails to start.
            interceptor = kwargs.get("_interceptor")
            improvement_session = interceptor.session_id if interceptor else None
            # Generate diffs now while files are still in their new state so
            # they're available for the LLM synthesis step on recovery.
            if improvement_session and interceptor:
                try:
                    interceptor.generate_diffs()
                except Exception:
                    pass
            # Carry the run_id forward so the supervisor knows this retry
            # belongs to the same failure run and appends to the same log group.
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
                # Preserve deferred_task across self-correction retries so the
                # clean worker can resume it once the fix succeeds.
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
                logger.info(
                    "Saved continuation → %s (improvement_session=%s)",
                    RESTART_FILE,
                    improvement_session,
                )
            except Exception as e:
                return ToolResult.error_result(f"Could not save restart continuation: {e}")
            msg += f"\nWill continue after restart: {continue_with[:100]}"
        graceful_fn = kwargs.get("_graceful_restart_fn")
        on_restart = getattr(getattr(agent, "gateway", None), "on_restart", None)
        if callable(on_restart):
            asyncio.ensure_future(on_restart())
        else:
            asyncio.ensure_future(_do_restart(graceful_fn=graceful_fn))
        return ToolResult.success_result(f"{msg}\nRestart initiated.", metadata={"stop_loop": True})

    return ToolResult.success_result(msg)

"""control_center tool — inspect and update runtime settings from within the agent."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from operator_use.tool.types import Tool, ToolContext, ToolExecutionMode, ToolInvocation, ToolKind, ToolResult

# Keys the agent is allowed to read and write.
# Each entry: (getter_name, setter_name | None, description, reload_required)
_KEYS: dict[str, tuple[str, str | None, str, bool]] = {
    # Feature toggles — require reload so the tool list is rebuilt
    "cron_enabled":         ("get_cron_enabled",         "set_cron_enabled",         "Enable/disable the cron scheduler.",             True),
    "subagents_enabled":    ("get_subagents_enabled",    "set_subagents_enabled",    "Enable/disable background subagent delegation.", True),
    "workflows_enabled":    ("get_workflows_enabled",    "set_workflows_enabled",    "Enable/disable workflow execution.",              True),
    "computer_use_enabled": ("get_computer_use_enabled", "set_computer_use_enabled", "Enable/disable desktop computer control.",       True),
    "browser_use_enabled":  ("get_browser_use_enabled",  "set_browser_use_enabled",  "Enable/disable browser automation.",             True),
    "extensions_enabled":   ("is_extensions_enabled",   "set_extensions_enabled",   "Enable/disable all extensions globally.",        True),
    "compaction_enabled":   ("get_compaction_enabled",  "set_compaction_enabled",   "Enable/disable context compaction.",             False),
    "retry_enabled":        ("get_retry_enabled",        "set_retry_enabled",        "Enable/disable LLM request retries.",            False),
    # Model / provider — no reload needed; picked up at next turn
    "default_provider":     ("get_default_provider",    "set_default_provider",     "Default LLM provider (e.g. 'anthropic').",       False),
    "default_model":        ("get_default_model",        "set_default_model",        "Default model ID (e.g. 'claude-opus-4-7').",     False),
}

_READABLE_KEYS = sorted(_KEYS)


class ControlCenterSchema(BaseModel):
    action: Literal["get", "set", "reboot"] = Field(
        description=(
            "get    — read one or all settings. Omit `key` to list all.\n"
            "set    — update a setting by key and value, then apply it.\n"
            "reboot — flush settings, shut down services, and restart the process. "
            "         Use after modifying source code or when a full restart is needed."
        )
    )
    key: str | None = Field(
        default=None,
        description=(
            "Setting key. Required for action=set. Available keys:\n"
            + "\n".join(f"  {k} — {_KEYS[k][2]}" for k in _READABLE_KEYS)
        ),
    )
    value: Any | None = Field(
        default=None,
        description="New value for the setting. Required for action=set. Use bool for toggles, str for model/provider.",
    )
    resume_prompt: str | None = Field(
        default=None,
        description=(
            "For action=reboot only. If provided, this message is injected into the agent "
            "automatically after restart so the task continues without waiting for human input. "
            "State only the next action to take — do NOT claim what was already done, "
            "as fabricated history will mislead the restarted agent."
        ),
    )

    @model_validator(mode="after")
    def _validate(self) -> ControlCenterSchema:
        if self.action == "set":
            if not self.key:
                raise ValueError("'key' is required when action='set'")
            if self.key not in _KEYS:
                raise ValueError(f"Unknown key {self.key!r}. Valid keys: {', '.join(_READABLE_KEYS)}")
            if _KEYS[self.key][1] is None:
                raise ValueError(f"Key {self.key!r} is read-only.")
            if self.value is None:
                raise ValueError("'value' is required when action='set'")
        return self


class ControlCenterTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="control_center",
            description=(
                "Inspect and update runtime settings for this harness instance. "
                "Use action='get' to read current values (omit key to see all), "
                "action='set' to change a setting (feature-flag changes reload the tool list "
                "so newly enabled tools become available next turn), and action='reboot' to "
                "fully restart the process — useful after editing source code."
            ),
            schema=ControlCenterSchema,
            kind=ToolKind.Unknown,
            execution_mode=ToolExecutionMode.Sequential,
        )
        # Strong refs to fire-and-forget tasks: the loop only holds weak refs,
        # so without this a scheduled task can be GC'd mid-flight.
        self._tasks: set[asyncio.Task] = set()

    def is_available(self, context: ToolContext) -> bool:
        return context.settings_manager is not None

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        sm = context.settings_manager if context else None
        if sm is None:
            return ToolResult.error(id=invocation.id, content="control_center: settings manager is not available.")

        try:
            params = ControlCenterSchema.model_validate(invocation.params)
        except Exception as exc:
            return ToolResult.error(id=invocation.id, content=str(exc))

        match params.action:
            case "get":
                return self._get(invocation, sm, params.key)
            case "set":
                return await self._set(invocation, sm, params.key, params.value, context)
            case "reboot":
                return self._schedule_reboot(invocation, sm, params.resume_prompt, context)
            case _:
                return ToolResult.error(id=invocation.id, content=f"Unknown action {params.action!r}.")

    # ------------------------------------------------------------------

    def _get(self, invocation: ToolInvocation, sm: Any, key: str | None) -> ToolResult:
        if key is not None:
            if key not in _KEYS:
                return ToolResult.error(id=invocation.id, content=f"Unknown key {key!r}.")
            getter, _setter, desc, reload_req = _KEYS[key]
            value = getattr(sm, getter)()
            row = {"key": key, "value": value, "description": desc, "reload_on_change": reload_req}
            return ToolResult.ok(id=invocation.id, content=json.dumps(row, indent=2))

        rows = []
        for k in _READABLE_KEYS:
            getter, setter, desc, reload_req = _KEYS[k]
            try:
                value = getattr(sm, getter)()
            except Exception:
                value = None
            rows.append({
                "key": k,
                "value": value,
                "description": desc,
                "readonly": setter is None,
                "reload_on_change": reload_req,
            })
        return ToolResult.ok(id=invocation.id, content=json.dumps(rows, indent=2))

    async def _set(
        self,
        invocation: ToolInvocation,
        sm: Any,
        key: str | None,
        value: Any,
        context: ToolContext | None,
    ) -> ToolResult:
        getter, setter_name, _desc, reload_required = _KEYS[key]  # type: ignore[index]
        assert setter_name is not None  # validated in schema

        # Coerce value to the right type based on the current setting type
        current = getattr(sm, getter)()
        if isinstance(current, bool) or value in (True, False, "true", "false"):
            if isinstance(value, str):
                value = value.lower() == "true"
            else:
                value = bool(value)

        try:
            getattr(sm, setter_name)(value)
        except Exception as exc:
            return ToolResult.error(id=invocation.id, content=f"control_center: failed to set {key!r}: {exc}")

        msg = f"Set {key!r} = {value!r}."

        if reload_required and context is not None:
            agent = context.agent
            runtime = getattr(agent, "_runtime", None) if agent else None
            if runtime is not None:
                try:
                    await runtime.reload()
                    msg += " Runtime reloaded — change takes effect next turn."
                except Exception as exc:
                    msg += f" (reload failed: {exc})"
            else:
                msg += " (runtime unavailable — restart to apply change)"

        return ToolResult.ok(id=invocation.id, content=msg)

    def _schedule_reboot(
        self,
        invocation: ToolInvocation,
        sm: Any,
        resume_prompt: str | None,
        context: ToolContext | None,
    ) -> ToolResult:
        """Register a deferred reboot on the engine and return immediately.

        The tool result is written to the session JSONL before the reboot fires,
        so the conversation history is complete when the new process resumes.
        """
        engine = context.engine if context else None
        if engine is None:
            return ToolResult.error(
                id=invocation.id,
                content="Reboot unavailable: no engine context.",
            )

        snapshot = self._snapshot_changes(context)

        async def _do_reboot() -> None:
            await self._reboot(sm, resume_prompt, context, snapshot)

        engine._deferred_fn = _do_reboot

        msg = "Reboot scheduled — the process will restart once this turn is saved."
        if resume_prompt:
            msg += f'\nResume prompt: "{resume_prompt}"'
        return ToolResult(id=invocation.id, content=msg, terminate=True)

    async def _reboot(
        self,
        sm: Any,
        resume_prompt: str | None,
        context: ToolContext | None,
        snapshot: dict,
    ) -> None:
        import asyncio
        import os
        import subprocess
        import sys
        from pathlib import Path

        # Capture session file before anything else
        session_file: Path | None = None
        if context is not None and context.session_manager is not None:
            sf = getattr(context.session_manager, "session_file", None)
            if sf is not None:
                session_file = Path(sf)

        # Flush settings to disk
        try:
            await sm.flush()
        except Exception:
            pass

        # Build argv
        argv = list(sys.argv)
        if session_file and "--session-file" not in argv:
            argv.extend(["--session-file", str(session_file)])
        if resume_prompt and "--prompt" not in argv:
            argv.extend(["--prompt", resume_prompt])

        # Create ready pipe — child writes "ready" when gateway is up
        read_fd, write_fd = os.pipe()
        env = {**os.environ, "OPERATOR_READY_FD": str(write_fd)}

        proc = subprocess.Popen(
            [sys.executable] + argv,
            env=env,
            stderr=subprocess.PIPE,
            pass_fds=(write_fd,),
        )
        os.close(write_fd)  # parent keeps only the read end

        # Wait for ready signal using asyncio (non-blocking)
        loop = asyncio.get_event_loop()
        ready_future: asyncio.Future[bytes] = loop.create_future()

        def _on_readable() -> None:
            loop.remove_reader(read_fd)
            try:
                data = os.read(read_fd, 64)
                if not ready_future.done():
                    ready_future.set_result(data)
            except Exception as exc:
                if not ready_future.done():
                    ready_future.set_exception(exc)

        loop.add_reader(read_fd, _on_readable)

        ready = False
        try:
            data = await asyncio.wait_for(asyncio.shield(ready_future), timeout=30.0)
            ready = data.strip() == b"ready"
        except (asyncio.TimeoutError, Exception):
            loop.remove_reader(read_fd)
        finally:
            try:
                os.close(read_fd)
            except OSError:
                pass

        if ready:
            # New process is up — shut down this process cleanly
            agent = context.agent if context else None
            runtime = getattr(agent, "_runtime", None) if agent else None
            if runtime is not None:
                try:
                    await asyncio.wait_for(runtime.ashutdown(), timeout=5.0)
                except Exception:
                    pass
            sys.exit(0)

        # Child failed — revert changes and inject an error message as a follow-up
        # so the agent can diagnose and retry (we are already past the tool-result
        # stage, so we cannot return a ToolResult here).
        proc.terminate()
        error_log = ""
        try:
            stderr_bytes, _ = proc.communicate(timeout=5.0)
            error_log = stderr_bytes.decode("utf-8", errors="replace").strip()
        except Exception:
            pass

        revert_msg = self._restore_snapshot(snapshot, context)

        error_text = (
            f"Reboot failed — new process exited before becoming ready.\n\n"
            f"Error output:\n{error_log or '(none captured)'}\n\n"
            f"{revert_msg}"
            f"Fix the error and call reboot again."
        )

        # Inject as a follow-up user message so the agent sees the failure
        agent = context.agent if context else None
        engine = context.engine if context else None
        if engine is not None and engine.state.follow_up_queue is not None:
            from operator_use.message.types import UserMessage, TextContent
            await engine.state.follow_up_queue.enqueue(
                UserMessage(contents=[TextContent(content=error_text)])
            )
        elif agent is not None:
            # Fallback: schedule a new turn with the error as user input
            def _spawn() -> None:
                task = asyncio.ensure_future(agent.invoke(error_text))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            asyncio.get_event_loop().call_soon(_spawn)

    def _snapshot_changes(self, context: ToolContext | None) -> dict[str, str]:
        """Read the content of every file modified since the last commit."""
        import subprocess
        from pathlib import Path
        cwd = self._cwd(context)
        snapshot: dict[str, str] = {}
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=cwd, capture_output=True, text=True,
            )
            if result.returncode != 0:
                return snapshot
            for rel in result.stdout.splitlines():
                rel = rel.strip()
                if not rel:
                    continue
                path = Path(cwd or ".") / rel
                try:
                    snapshot[str(path)] = path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
        except Exception:
            pass
        return snapshot

    def _restore_snapshot(self, snapshot: dict[str, str], context: ToolContext | None) -> str:
        """Write snapshot content back to disk — reboot failed, revert the changes."""
        from pathlib import Path
        if not snapshot:
            return ""
        failed: list[str] = []
        for path_str, content in snapshot.items():
            try:
                Path(path_str).write_text(content, encoding="utf-8")
            except Exception as exc:
                failed.append(f"{path_str}: {exc}")
        if failed:
            return "Warning: could not revert some files:\n" + "\n".join(failed) + "\n\n"
        return f"Reverted {len(snapshot)} file(s) to pre-reboot state.\n\n"

    def _cwd(self, context: ToolContext | None) -> str | None:
        """Return the working directory string from context."""
        if context and context.session_manager:
            return str(getattr(context.session_manager, "cwd", None) or ".")
        return None


tool = ControlCenterTool()

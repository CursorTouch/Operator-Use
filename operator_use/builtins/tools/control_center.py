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
    "cron":                 ("get_cron_enabled",         "set_cron_enabled",         "Enable/disable the cron scheduler.",             True),
    "subagent":             ("get_subagents_enabled",    "set_subagents_enabled",    "Enable/disable background subagent delegation.", True),
    "workflow":             ("get_workflows_enabled",    "set_workflows_enabled",    "Enable/disable workflow execution.",              True),
    "computer_use":         ("get_computer_use_enabled", "set_computer_use_enabled", "Enable/disable desktop computer control.",       True),
    "browser_use":          ("get_browser_use_enabled",  "set_browser_use_enabled",  "Enable/disable browser automation.",             True),
    "extensions":           ("is_extensions_enabled",   "set_extensions_enabled",   "Enable/disable all extensions globally.",        True),
    "compaction":           ("get_compaction_enabled",  "set_compaction_enabled",   "Enable/disable context compaction.",             False),
    "retry":                ("get_retry_enabled",        "set_retry_enabled",        "Enable/disable LLM request retries.",            False),
    # Model / provider — no reload needed; picked up at next turn
    "default_provider":     ("get_default_provider",    "set_default_provider",     "Default LLM provider (e.g. 'anthropic').",       False),
    "default_model":        ("get_default_model",        "set_default_model",        "Default model ID (e.g. 'claude-opus-4-7').",     False),
    # STT — no reload needed; hooks read settings dynamically each message
    "stt_enabled":          ("get_stt_enabled",          "set_stt_enabled",          "Enable/disable speech-to-text transcription.",   False),
    "stt_model":            ("get_stt_model",             "set_stt_model",            "STT model ID (e.g. 'whisper-large-v3-turbo').",  False),
    "stt_provider":         ("get_stt_provider",          "set_stt_provider",         "STT provider (e.g. 'groq', 'openai').",          False),
    # TTS — no reload needed; hooks read settings dynamically each message
    "tts_enabled":          ("get_tts_enabled",          "set_tts_enabled",          "Enable/disable text-to-speech synthesis.",        False),
    "tts_voice":            ("get_tts_voice",             "set_tts_voice",            "TTS voice name (e.g. 'autumn', 'alloy').",       False),
    "tts_model":            ("get_tts_model",             "set_tts_model",            "TTS model ID (e.g. 'canopylabs/orpheus-v1-english').", False),
    "tts_provider":         ("get_tts_provider",          "set_tts_provider",         "TTS provider (e.g. 'groq', 'openai').",          False),
}

# Agent-level keys — read/written directly on the running agent, not via settings manager.
# Each entry: (description,)
_AGENT_KEYS: dict[str, str] = {
    "model":    "Current session LLM model ID (e.g. 'claude-sonnet-4-6'). Takes effect next turn.",
    "provider": "Current session LLM provider (e.g. 'anthropic'). Takes effect next turn.",
}

_READABLE_KEYS = sorted({**_KEYS, **_AGENT_KEYS})


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
            + "\n".join(
                f"  {k} — {_KEYS[k][2]}" if k in _KEYS else f"  {k} — {_AGENT_KEYS[k]}"
                for k in _READABLE_KEYS
            )
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
            if self.key not in _KEYS and self.key not in _AGENT_KEYS:
                raise ValueError(f"Unknown key {self.key!r}. Valid keys: {', '.join(_READABLE_KEYS)}")
            if self.key in _KEYS and _KEYS[self.key][1] is None:
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

    def get_display_name(self, args: dict) -> str:
        action = args.get('action', '')
        key = args.get('key')
        resume_prompt = args.get('resume_prompt')
        if action == 'get':
            return f"Reading setting: {key}" if key else "Reading settings"
        if action == 'set':
            return f"Changing setting: {key}" if key else "Changing setting"
        if action == 'reboot':
            if resume_prompt:
                short = resume_prompt[:40].rstrip()
                return f"Rebooting → {short}…"
            return "Rebooting"
        return "Settings"

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
                return self._get(invocation, sm, params.key, context)
            case "set":
                return await self._set(invocation, sm, params.key, params.value, context)
            case "reboot":
                return self._schedule_reboot(invocation, sm, params.resume_prompt, context)
            case _:
                return ToolResult.error(id=invocation.id, content=f"Unknown action {params.action!r}.")

    # ------------------------------------------------------------------

    def _get_agent_value(self, key: str, context: ToolContext | None) -> Any:
        agent = context.agent if context else None
        llm = getattr(getattr(agent, '_engine', None), 'llm', None)
        if llm is None:
            return None
        if key == "model":
            return llm.model.id
        if key == "provider":
            return llm.model.provider
        return None

    def _get(self, invocation: ToolInvocation, sm: Any, key: str | None, context: ToolContext | None = None) -> ToolResult:
        if key is not None:
            if key not in _KEYS and key not in _AGENT_KEYS:
                return ToolResult.error(
                    id=invocation.id, content=f"Unknown key {key!r}.",
                    metadata={'display_name': f"Unknown setting: {key}"},
                )
            if key in _AGENT_KEYS:
                desc = _AGENT_KEYS[key]
                value = self._get_agent_value(key, context)
                row = {"key": key, "value": value, "description": desc}
            else:
                getter, _setter, desc, reload_req = _KEYS[key]
                value = getattr(sm, getter)()
                row = {"key": key, "value": value, "description": desc, "reload_on_change": reload_req}
            return ToolResult.ok(
                id=invocation.id, content=json.dumps(row, indent=2),
                metadata={'display_name': f"Read setting: {key}"},
            )

        rows = []
        for k in _READABLE_KEYS:
            try:
                if k in _AGENT_KEYS:
                    value = self._get_agent_value(k, context)
                    rows.append({"key": k, "value": value, "description": _AGENT_KEYS[k]})
                else:
                    getter, setter, desc, reload_req = _KEYS[k]
                    value = getattr(sm, getter)()
                    rows.append({"key": k, "value": value, "description": desc, "readonly": setter is None, "reload_on_change": reload_req})
            except Exception:
                rows.append({"key": k, "value": None})
        return ToolResult.ok(
            id=invocation.id, content=json.dumps(rows, indent=2),
            metadata={'display_name': "Settings read"},
        )

    async def _set(
        self,
        invocation: ToolInvocation,
        sm: Any,
        key: str | None,
        value: Any,
        context: ToolContext | None,
    ) -> ToolResult:
        # Agent-level keys: swap the running LLM in-place
        if key in _AGENT_KEYS:
            return await self._set_agent_key(invocation, key, value, context)

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
            return ToolResult.error(
                id=invocation.id, content=f"control_center: failed to set {key!r}: {exc}",
                metadata={'display_name': f"Failed to change: {key}"},
            )

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

        return ToolResult.ok(
            id=invocation.id, content=msg,
            metadata={'display_name': f"Changed setting: {key}"},
        )

    async def _set_agent_key(
        self,
        invocation: ToolInvocation,
        key: str,
        value: Any,
        context: ToolContext | None,
    ) -> ToolResult:
        agent = context.agent if context else None
        engine = getattr(agent, '_engine', None)
        if engine is None:
            return ToolResult.error(id=invocation.id, content="control_center: agent engine unavailable.")

        current_llm = engine.llm
        current_model_id = current_llm.model.id
        current_provider = current_llm.model.provider

        new_model_id = str(value) if key == "model" else current_model_id
        new_provider = str(value) if key == "provider" else current_provider

        try:
            from operator_use.inference.api.text.service import LLM
            engine.llm = LLM(
                model_id=new_model_id,
                provider=new_provider,
                auth_store=current_llm._auth_store,
            )
            # Keep baseline in sync so reloads don't revert to the old model.
            if agent is not None and hasattr(agent, '_baseline_llm'):
                agent._baseline_llm = engine.llm
        except Exception as exc:
            return ToolResult.error(
                id=invocation.id,
                content=f"control_center: failed to switch {key!r} to {value!r}: {exc}",
                metadata={'display_name': f"Failed to change: {key}"},
            )

        return ToolResult.ok(
            id=invocation.id,
            content=f"Switched to model={new_model_id!r}, provider={new_provider!r}. Takes effect next turn.",
            metadata={'display_name': f"Changed {key}: {value}"},
        )

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
                metadata={'display_name': "Reboot failed"},
            )

        snapshot = self._snapshot_changes(context)

        # Capture the channel + chat this reboot was requested from so the resume
        # prompt can be delivered back there instead of the default stdio terminal.
        origin_channel = origin_chat = None
        if resume_prompt:
            try:
                from operator_use.subagent.manager import _session_channel, _session_chat_id
                origin_channel = _session_channel.get()
                origin_chat = _session_chat_id.get()
            except Exception:
                pass

        async def _do_reboot() -> None:
            await self._reboot(sm, resume_prompt, context, snapshot, origin_channel, origin_chat)

        engine._deferred_fn = _do_reboot

        msg = "Reboot complete. You are the new process. Resume normally from this point."
        if resume_prompt:
            msg += f'\nResume prompt: "{resume_prompt}"'
        end_label = f"Rebooted → {resume_prompt[:40].rstrip()}…" if resume_prompt else "Rebooted"
        return ToolResult(
            id=invocation.id,
            content=msg,
            terminate=True,
            terminate_message="Back up and running — ready for what's next.",
            metadata={'display_name': end_label},
        )

    async def _reboot(
        self,
        sm: Any,
        resume_prompt: str | None,
        context: ToolContext | None,
        snapshot: dict,
        origin_channel: str | None = None,
        origin_chat: str | None = None,
    ) -> None:
        import os
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

        # Let the bus drain so the channel can finish editing the tool-status message
        # to ✅/❌ before we tear down the gateway. The tool_end event was published
        # to the bus synchronously, but the channel's outgoing API call (Telegram
        # editMessageText, Discord message.edit, etc.) is async and in-flight. Without
        # this pause ashutdown() cancels those tasks before they complete, leaving the
        # ⚙️ spinner stuck forever.
        await asyncio.sleep(2.0)

        # Stop the gateway channel pooling BEFORE spawning the child so
        # there is never a window where both processes poll the same bot token.
        # Telegram rejects concurrent getUpdates with a Conflict error.
        agent = context.agent if context else None
        runtime = getattr(agent, "_runtime", None) if agent else None
        if runtime is not None:
            try:
                await runtime.ashutdown()
            except Exception:
                pass

        # Build argv — pin cwd and session file so the new image resumes
        # exactly where this one left off. Strip any stale --prompt so a startup
        # or previous-reboot prompt never replays; the resume prompt is passed via
        # the environment below instead (see OPERATOR_PROMPT*).
        argv = self._strip_flags(list(sys.argv), ("--prompt",))
        if "--cwd" not in argv:
            argv.extend(["--cwd", str(Path.cwd())])
        if session_file and "--session-file" not in argv:
            argv.extend(["--session-file", str(session_file)])

        # Replace this process image with a fresh one via exec.
        # This avoids all subprocess / process-group / SIGHUP / Conflict issues
        # that come from spawning a child and killing the parent:
        #   - same PID, same terminal, same foreground process
        #   - no SIGHUP sent to a child process group
        #   - no window where two processes poll the same channel simultaneously
        #   - no os._exit() traceback leaking through the asyncio task stack
        # Strip OPERATOR_READY_FD from the environment — there is no parent
        # waiting for a "ready" signal when we exec in place.
        # Strip OPERATOR_READY_FD (no parent awaits us after exec-in-place) plus any
        # stale prompt vars, then set fresh resume-prompt routing. Passing the prompt
        # via the env (not argv) keeps it one-shot — it never lingers to replay on a
        # later reboot, and carries the originating channel so the reply lands there.
        env = {
            k: v for k, v in os.environ.items()
            if k not in ("OPERATOR_READY_FD", "OPERATOR_PROMPT", "OPERATOR_PROMPT_CHANNEL", "OPERATOR_PROMPT_CHAT")
        }
        if resume_prompt:
            env["OPERATOR_PROMPT"] = resume_prompt
            if origin_channel:
                env["OPERATOR_PROMPT_CHANNEL"] = origin_channel
            if origin_chat:
                env["OPERATOR_PROMPT_CHAT"] = origin_chat
        try:
            os.execve(sys.executable, [sys.executable] + argv, env)
        except OSError as exc:
            revert_msg = self._restore_snapshot(snapshot, context)
            error_text = (
                f"Reboot failed — could not exec new process: {exc}\n\n"
                f"{revert_msg}"
                f"Fix the error and call reboot again."
            )
            engine = context.engine if context else None
            if engine is not None and engine.state.follow_up_queue is not None:
                from operator_use.message.types import UserMessage, TextContent
                await engine.state.follow_up_queue.enqueue(
                    UserMessage(contents=[TextContent(content=error_text)])
                )

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

    @staticmethod
    def _strip_flags(argv: list[str], flags: tuple[str, ...]) -> list[str]:
        """Drop each `--flag value` pair from argv (used to remove stale reboot flags)."""
        out: list[str] = []
        i = 0
        while i < len(argv):
            if argv[i] in flags:
                i += 2  # skip the flag and its value
                continue
            out.append(argv[i])
            i += 1
        return out


tool = ControlCenterTool()

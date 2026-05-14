from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from program.extensions.runtime import emit_session_shutdown_event
from program.session.manager import SessionManager

if TYPE_CHECKING:
    from program.agent.services import AgentSessionRuntimeDiagnostic, AgentSessionServices
    from program.agent.session import AgentSession


class SessionImportFileNotFoundError(Exception):
    def __init__(self, file_path: str) -> None:
        super().__init__(f"File not found: {file_path}")
        self.file_path = file_path


class MissingSessionCwdError(Exception):
    pass


def assert_session_cwd_exists(session_manager: SessionManager, fallback_cwd: str) -> None:
    cwd = session_manager.get_cwd()
    if not cwd or not Path(cwd).exists():
        raise MissingSessionCwdError(
            f"Session cwd does not exist: {cwd!r}. "
            "Use cwd_override to specify an existing directory."
        )


def _extract_user_message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


CreateAgentSessionRuntimeFactory = Callable[..., "CreateAgentSessionRuntimeResult"]


class CreateAgentSessionRuntimeResult:
    def __init__(
        self,
        session: "AgentSession",
        services: "AgentSessionServices",
        diagnostics: list,
        model_fallback_message: Optional[str] = None,
    ) -> None:
        self.session = session
        self.services = services
        self.diagnostics = diagnostics
        self.model_fallback_message = model_fallback_message


class AgentSessionRuntime:
    """
    Owns the current AgentSession plus its cwd-bound services.

    Session replacement methods tear down the current runtime first, then create
    and apply the next runtime.
    """

    def __init__(
        self,
        session: "AgentSession",
        services: "AgentSessionServices",
        create_runtime: CreateAgentSessionRuntimeFactory,
        diagnostics: Optional[list["AgentSessionRuntimeDiagnostic"]] = None,
        model_fallback_message: Optional[str] = None,
    ) -> None:
        self._session = session
        self._services = services
        self._create_runtime = create_runtime
        self._diagnostics: list = list(diagnostics or [])
        self._model_fallback_message = model_fallback_message
        self._rebind_session: Optional[Callable] = None
        self._before_session_invalidate: Optional[Callable] = None

    @property
    def services(self) -> "AgentSessionServices":
        return self._services

    @property
    def session(self) -> "AgentSession":
        return self._session

    @property
    def cwd(self) -> str:
        return self._services.cwd

    @property
    def diagnostics(self) -> list:
        return list(self._diagnostics)

    @property
    def model_fallback_message(self) -> Optional[str]:
        return self._model_fallback_message

    def set_rebind_session(self, rebind_session: Optional[Callable] = None) -> None:
        self._rebind_session = rebind_session

    def set_before_session_invalidate(self, callback: Optional[Callable] = None) -> None:
        """
        Set a synchronous callback that runs after session_shutdown handlers finish
        but before the current session is invalidated.
        """
        self._before_session_invalidate = callback

    async def _emit_before_switch(
        self, reason: str, target_session_file: Optional[str] = None
    ) -> dict:
        runner = self.session.extension_runner
        if not runner.has_handlers("session_before_switch"):
            return {"cancelled": False}
        result = await runner.emit({
            "type": "session_before_switch",
            "reason": reason,
            "targetSessionFile": target_session_file,
        })
        cancelled = bool(result.get("cancel")) if result else False
        return {"cancelled": cancelled}

    async def _emit_before_fork(self, entry_id: str, options: dict) -> dict:
        runner = self.session.extension_runner
        if not runner.has_handlers("session_before_fork"):
            return {"cancelled": False}
        result = await runner.emit({
            "type": "session_before_fork",
            "entryId": entry_id,
            **options,
        })
        cancelled = bool(result.get("cancel")) if result else False
        return {"cancelled": cancelled}

    async def _teardown_current(
        self, reason: str, target_session_file: Optional[str] = None
    ) -> None:
        await emit_session_shutdown_event(
            self.session.extension_runner,
            {"type": "session_shutdown", "reason": reason, "targetSessionFile": target_session_file},
        )
        if self._before_session_invalidate:
            self._before_session_invalidate()
        self.session.dispose()

    def _apply(self, result: "CreateAgentSessionRuntimeResult") -> None:
        self._session = result.session
        self._services = result.services
        self._diagnostics = list(result.diagnostics)
        self._model_fallback_message = result.model_fallback_message

    async def _finish_session_replacement(
        self, with_session: Optional[Callable] = None
    ) -> None:
        if self._rebind_session:
            await self._rebind_session(self.session)
        if with_session:
            await with_session(self.session.create_replaced_session_context())

    async def switch_session(
        self,
        session_path: str,
        options: Optional[dict] = None,
    ) -> dict:
        options = options or {}
        before = await self._emit_before_switch("resume", session_path)
        if before["cancelled"]:
            return before

        previous_session_file = self.session.session_file
        cwd_override = options.get("cwd_override")
        session_manager = SessionManager.open(session_path, None, cwd_override)
        assert_session_cwd_exists(session_manager, self.cwd)

        await self._teardown_current("resume", session_manager.get_session_file())
        self._apply(await self._create_runtime(
            cwd=session_manager.get_cwd(),
            agent_dir=self._services.agent_dir,
            session_manager=session_manager,
            session_start_event={
                "type": "session_start",
                "reason": "resume",
                "previousSessionFile": previous_session_file,
            },
        ))
        await self._finish_session_replacement(options.get("with_session"))
        return {"cancelled": False}

    async def new_session(self, options: Optional[dict] = None) -> dict:
        options = options or {}
        before = await self._emit_before_switch("new")
        if before["cancelled"]:
            return before

        previous_session_file = self.session.session_file
        session_dir = self.session.session_manager.get_session_dir()
        session_manager = SessionManager.create(self.cwd, session_dir)

        if options.get("parent_session"):
            session_manager.new_session(parent_session=options["parent_session"])

        await self._teardown_current("new", session_manager.get_session_file())
        self._apply(await self._create_runtime(
            cwd=self.cwd,
            agent_dir=self._services.agent_dir,
            session_manager=session_manager,
            session_start_event={
                "type": "session_start",
                "reason": "new",
                "previousSessionFile": previous_session_file,
            },
        ))

        if options.get("setup"):
            await options["setup"](self.session.session_manager)
            ctx = self.session.session_manager.build_session_context()
            self.session.agent.state.messages = ctx.messages

        await self._finish_session_replacement(options.get("with_session"))
        return {"cancelled": False}

    async def fork(
        self, entry_id: str, options: Optional[dict] = None
    ) -> dict:
        options = options or {}
        position = options.get("position", "before")
        before = await self._emit_before_fork(entry_id, {"position": position})
        if before["cancelled"]:
            return {"cancelled": True}

        selected_entry = self.session.session_manager.get_entry(entry_id)
        if not selected_entry:
            raise ValueError("Invalid entry ID for forking")

        selected_text: Optional[str] = None

        if position == "at":
            target_leaf_id = selected_entry.id
        else:
            if selected_entry.type != "message" or selected_entry.message.role != "user":
                raise ValueError("Invalid entry ID for forking: must be a user message")
            target_leaf_id = selected_entry.parent_id
            selected_text = _extract_user_message_text(selected_entry.message.content)

        previous_session_file = self.session.session_file

        if self.session.session_manager.is_persisted():
            current_session_file = self.session.session_file
            if not current_session_file:
                raise RuntimeError("Persisted session is missing a session file")
            session_dir = self.session.session_manager.get_session_dir()

            if not target_leaf_id:
                session_manager = SessionManager.create(self.cwd, session_dir)
                session_manager.new_session(parent_session=current_session_file)
            else:
                source_manager = SessionManager.open(current_session_file, session_dir)
                forked_path = source_manager.create_branched_session(target_leaf_id)
                if not forked_path:
                    raise RuntimeError("Failed to create forked session")
                session_manager = SessionManager.open(forked_path, session_dir)

            await self._teardown_current("fork", session_manager.get_session_file())
            self._apply(await self._create_runtime(
                cwd=session_manager.get_cwd(),
                agent_dir=self._services.agent_dir,
                session_manager=session_manager,
                session_start_event={
                    "type": "session_start",
                    "reason": "fork",
                    "previousSessionFile": previous_session_file,
                },
            ))
            await self._finish_session_replacement(options.get("with_session"))
            return {"cancelled": False, "selected_text": selected_text}

        # In-memory fork
        session_manager = self.session.session_manager
        if not target_leaf_id:
            session_manager.new_session(parent_session=self.session.session_file)
        else:
            session_manager.create_branched_session(target_leaf_id)

        await self._teardown_current("fork", session_manager.get_session_file())
        self._apply(await self._create_runtime(
            cwd=self.cwd,
            agent_dir=self._services.agent_dir,
            session_manager=session_manager,
            session_start_event={
                "type": "session_start",
                "reason": "fork",
                "previousSessionFile": previous_session_file,
            },
        ))
        await self._finish_session_replacement(options.get("with_session"))
        return {"cancelled": False, "selected_text": selected_text}

    async def import_from_jsonl(
        self, input_path: str, cwd_override: Optional[str] = None
    ) -> dict:
        resolved_path = str(Path(input_path).resolve())
        if not Path(resolved_path).exists():
            raise SessionImportFileNotFoundError(resolved_path)

        session_dir = self.session.session_manager.get_session_dir()
        Path(session_dir).mkdir(parents=True, exist_ok=True)

        destination_path = str(Path(session_dir) / Path(resolved_path).name)
        before = await self._emit_before_switch("resume", destination_path)
        if before["cancelled"]:
            return before

        previous_session_file = self.session.session_file
        if str(Path(destination_path).resolve()) != resolved_path:
            shutil.copy2(resolved_path, destination_path)

        session_manager = SessionManager.open(destination_path, session_dir, cwd_override)
        assert_session_cwd_exists(session_manager, self.cwd)

        await self._teardown_current("resume", session_manager.get_session_file())
        self._apply(await self._create_runtime(
            cwd=session_manager.get_cwd(),
            agent_dir=self._services.agent_dir,
            session_manager=session_manager,
            session_start_event={
                "type": "session_start",
                "reason": "resume",
                "previousSessionFile": previous_session_file,
            },
        ))
        await self._finish_session_replacement()
        return {"cancelled": False}

    async def dispose(self) -> None:
        await emit_session_shutdown_event(
            self.session.extension_runner,
            {"type": "session_shutdown", "reason": "quit"},
        )
        if self._before_session_invalidate:
            self._before_session_invalidate()
        self.session.dispose()


async def create_agent_session_runtime(
    create_runtime: CreateAgentSessionRuntimeFactory,
    options: dict,
) -> AgentSessionRuntime:
    session_manager = options["session_manager"]
    assert_session_cwd_exists(session_manager, options["cwd"])
    result = await create_runtime(**options)
    return AgentSessionRuntime(
        result.session,
        result.services,
        create_runtime,
        result.diagnostics,
        result.model_fallback_message,
    )

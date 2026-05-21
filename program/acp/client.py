from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, TYPE_CHECKING

from acp import (
    PROTOCOL_VERSION,
    connect_to_agent,
    spawn_agent_process,
)
from acp.schema import (
    RequestPermissionResponse,
    ReadTextFileResponse,
    WriteTextFileResponse,
    CreateTerminalResponse,
    TerminalOutputResponse,
    ReleaseTerminalResponse,
    WaitForTerminalExitResponse,
    KillTerminalResponse,
    PermissionOption,
)

from program.acp.utils import content_blocks_from_text

if TYPE_CHECKING:
    from acp.agent.connection import AgentSideConnection as _AgentConn

logger = logging.getLogger(__name__)


class OperatorACPClient:
    """
    Implements the ``acp.Client`` protocol — the server side of Zed ACP.

    The SDK passes an AgentSideConnection via ``on_connect``; the agent then
    calls methods on this object to push streaming updates and request
    resources from the client environment.

    One instance is created per ``ACPClient`` session.
    """

    def __init__(self) -> None:
        self._chunks: asyncio.Queue[str | None] = asyncio.Queue()
        self._conn: _AgentConn | None = None

    # ── SDK lifecycle ─────────────────────────────────────────────────────────

    def on_connect(self, conn: Any) -> None:
        self._conn = conn

    # ── Streaming helper ──────────────────────────────────────────────────────

    async def drain(self) -> AsyncIterator[str]:
        """Yield text chunks until the agent signals end-of-turn (None sentinel)."""
        while True:
            chunk = await self._chunks.get()
            if chunk is None:
                break
            yield chunk

    # ── acp.Client protocol ───────────────────────────────────────────────────

    async def session_update(
        self,
        session_id: str,
        update: Any,
        **kwargs: Any,
    ) -> None:
        kind = getattr(update, 'session_update', '')
        content = getattr(update, 'content', None)
        text = getattr(content, 'text', None) if content is not None else None
        if text and kind in ('agent_message_chunk', 'agent_thought_chunk'):
            await self._chunks.put(text)

    async def request_permission(
        self,
        options: list[PermissionOption],
        session_id: str,
        tool_call: Any,
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        # Auto-allow all tool calls when acting as a programmatic client.
        if options:
            return RequestPermissionResponse(selected_option=options[0].id)
        return RequestPermissionResponse(selected_option='')

    async def write_text_file(
        self,
        content: str,
        path: str,
        session_id: str,
        **kwargs: Any,
    ) -> WriteTextFileResponse | None:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            return WriteTextFileResponse()
        except Exception as exc:
            logger.error('ACPClient: write_text_file failed: %s', exc)
            return None

    async def read_text_file(
        self,
        path: str,
        session_id: str,
        limit: int | None = None,
        line: int | None = None,
        **kwargs: Any,
    ) -> ReadTextFileResponse:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            if line is not None:
                start = max(0, line - 1)
                lines = lines[start:]
            if limit is not None:
                lines = lines[:limit]
            return ReadTextFileResponse(content=''.join(lines))
        except Exception as exc:
            logger.error('ACPClient: read_text_file failed: %s', exc)
            return ReadTextFileResponse(content='')

    async def create_terminal(self, command: str, session_id: str, **kwargs: Any) -> CreateTerminalResponse:
        return CreateTerminalResponse(terminal_id='')

    async def terminal_output(self, session_id: str, terminal_id: str, **kwargs: Any) -> TerminalOutputResponse:
        return TerminalOutputResponse(output='', done=True)

    async def release_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> ReleaseTerminalResponse | None:
        return None

    async def wait_for_terminal_exit(self, session_id: str, terminal_id: str, **kwargs: Any) -> WaitForTerminalExitResponse:
        return WaitForTerminalExitResponse(exit_code=0)

    async def kill_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> KillTerminalResponse | None:
        return None

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        pass


class ACPClient:
    """
    High-level Zed ACP client with transport auto-negotiation.

    Factory methods:
        ACPClient.stdio(command, *args)   — same-machine subprocess via stdio
        ACPClient.http(url)               — remote agent over HTTP/SSE
        ACPClient.discover(agent_id)      — auto-discover via ACPRegistry

    Usage (stdio)::

        async with ACPClient.stdio('operator', 'acp') as client:
            async with client.session() as session_id:
                text = await client.run('summarise auth.py', session_id)

    Usage (streaming)::

        async with ACPClient.stdio('operator', 'acp') as client:
            async with client.session() as session_id:
                async for chunk in client.run_stream('refactor auth.py', session_id):
                    print(chunk, end='', flush=True)
    """

    # ── Factory methods ───────────────────────────────────────────────────────

    @classmethod
    def stdio(cls, command: str, *args: str) -> ACPClient:
        """Create a client that connects via stdio subprocess."""
        return cls(_transport='stdio', _command=command, _args=args)

    @classmethod
    def http(cls, url: str, token: str | None = None) -> ACPClient:
        """Create a client that connects to a remote HTTP agent."""
        return cls(_transport='http', _url=url, _token=token)

    @classmethod
    def discover(cls, agent_id: str) -> ACPClient:
        """Create a client that auto-discovers a local agent via ACPRegistry."""
        return cls(_transport='discover', _agent_id=agent_id)

    # ── Init (private; use factory methods) ───────────────────────────────────

    def __init__(
        self,
        *,
        _transport: str = 'stdio',
        _command: str = '',
        _args: tuple[str, ...] = (),
        _url: str = '',
        _agent_id: str = '',
        _token: str | None = None,
    ) -> None:
        self._transport = _transport
        self._command = _command
        self._args = _args
        self._url = _url
        self._agent_id = _agent_id
        self._token = _token
        self._conn: Any = None  # ClientSideConnection
        self._process: Any = None
        self._acp_client = OperatorACPClient()
        self._session_id: str | None = None
        self._ctx: Any = None
        # HTTP transport resources
        self._http_client: Any = None
        self._sse_task: asyncio.Task[None] | None = None
        self._post_task: asyncio.Task[None] | None = None

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> ACPClient:
        if self._transport == 'stdio':
            self._ctx = spawn_agent_process(self._acp_client, self._command, *self._args)
            self._conn, self._process = await self._ctx.__aenter__()
        elif self._transport == 'http':
            await self._http_aenter()
        elif self._transport == 'discover':
            from program.acp.registry import ACPRegistry
            reg = ACPRegistry()
            entry = await reg.find(self._agent_id)
            if entry is None:
                raise RuntimeError(f'ACPClient.discover: agent {self._agent_id!r} not found in registry')
            transport = entry.get('transport', 'stdio')
            if transport == 'stdio':
                command = entry['command']
                args = entry.get('args', [])
                self._ctx = spawn_agent_process(self._acp_client, command, *args)
                self._conn, self._process = await self._ctx.__aenter__()
            else:
                raise NotImplementedError(f'Transport {transport!r} not supported via discover')
        await self._conn.initialize(protocol_version=PROTOCOL_VERSION)
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        if self._session_id:
            try:
                await self._conn.close_session(session_id=self._session_id)
            except Exception:
                pass
            self._session_id = None
        if self._ctx is not None:
            await self._ctx.__aexit__(*exc_info)
            self._ctx = None
        if self._sse_task is not None:
            self._sse_task.cancel()
            with contextlib.suppress(Exception):
                await self._sse_task
            self._sse_task = None
        if self._post_task is not None:
            self._post_task.cancel()
            with contextlib.suppress(Exception):
                await self._post_task
            self._post_task = None
        if self._http_client is not None:
            with contextlib.suppress(Exception):
                await self._http_client.aclose()
            self._http_client = None

    # ── HTTP transport ────────────────────────────────────────────────────────

    async def _http_aenter(self) -> None:
        """Set up HTTP transport using a socket pair to bridge SSE↔ACP."""
        import httpx

        base = self._url.rstrip('/')
        auth_headers: dict[str, str] = {}
        if self._token:
            auth_headers['Authorization'] = f'Bearer {self._token}'

        # Socket pair: ACP side ↔ bridge side
        # What ACP writes to acp_writer appears on bridge_reader (→ POST to server)
        # What we write to bridge_writer appears on acp_reader  (← SSE from server)
        sock_acp, sock_bridge = socket.socketpair()
        sock_acp.setblocking(False)
        sock_bridge.setblocking(False)

        acp_reader, acp_writer = await asyncio.open_connection(sock=sock_acp)
        bridge_reader, bridge_writer = await asyncio.open_connection(sock=sock_bridge)

        # connect_to_agent(client, input_stream=StreamWriter, output_stream=StreamReader)
        # input_stream  = process stdin  = we send outgoing messages TO acp_writer
        # output_stream = process stdout = ACP reads incoming messages FROM acp_reader
        self._conn = connect_to_agent(self._acp_client, acp_writer, acp_reader)

        self._http_client = httpx.AsyncClient(headers=auth_headers, timeout=60.0)

        # conn_id is returned by the server in X-ACP-Connection-ID header on the SSE
        # response; _sse_listener sets it so _post_sender can address the right slot.
        conn_id_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

        # Bridge 1: SSE events from server → bridge_writer → acp_reader (incoming to ACP)
        self._sse_task = asyncio.create_task(
            self._sse_listener(f'{base}/acp/events', bridge_writer, conn_id_future)
        )
        # Bridge 2: ACP outgoing → bridge_reader → POST /acp/rpc/{conn_id}
        self._post_task = asyncio.create_task(
            self._post_sender(f'{base}/acp/rpc', bridge_reader, conn_id_future)
        )

    async def _sse_listener(
        self,
        url: str,
        writer: asyncio.StreamWriter,
        conn_id_future: asyncio.Future[str],
    ) -> None:
        """Read SSE events from the remote server and feed them to the ACP connection."""
        try:
            async with self._http_client.stream('GET', url) as resp:
                resp.raise_for_status()
                conn_id = resp.headers.get('X-ACP-Connection-ID', '')
                if conn_id and not conn_id_future.done():
                    conn_id_future.set_result(conn_id)
                async for line in resp.aiter_lines():
                    if line.startswith('data: '):
                        data = line[6:].strip()
                        if data and data != '[DONE]':
                            writer.write(data.encode() + b'\n')
                            await writer.drain()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error('ACPClient HTTP: SSE listener error: %s', exc)
        finally:
            if not conn_id_future.done():
                conn_id_future.cancel()
            with contextlib.suppress(Exception):
                writer.close()

    async def _post_sender(
        self,
        base_url: str,
        reader: asyncio.StreamReader,
        conn_id_future: asyncio.Future[str],
    ) -> None:
        """Read outgoing ACP messages and POST each one to /acp/rpc/{conn_id}."""
        try:
            conn_id = await conn_id_future
            rpc_url = f'{base_url}/{conn_id}'
            while True:
                line = await reader.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                await self._http_client.post(
                    rpc_url,
                    content=line,
                    headers={'Content-Type': 'application/json'},
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error('ACPClient HTTP: POST sender error: %s', exc)

    # ── Session management ────────────────────────────────────────────────────

    @asynccontextmanager
    async def session(self, cwd: str | None = None) -> AsyncIterator[str]:
        """Async context manager that creates a session and yields the session_id."""
        import os as _os
        resp = await self._conn.new_session(cwd=cwd or _os.getcwd())
        session_id = resp.session_id
        self._session_id = session_id
        try:
            yield session_id
        finally:
            try:
                await self._conn.close_session(session_id=session_id)
            except Exception:
                pass
            if self._session_id == session_id:
                self._session_id = None

    # ── High-level API ────────────────────────────────────────────────────────

    async def run(self, text: str, session_id: str) -> str:
        """Send a prompt and return the full response text."""
        chunks: list[str] = []
        async for chunk in self.run_stream(text, session_id):
            chunks.append(chunk)
        return ''.join(chunks)

    async def run_stream(self, text: str, session_id: str) -> AsyncIterator[str]:
        """Send a prompt and yield text chunks as they arrive."""
        self._acp_client._chunks = asyncio.Queue()
        acp_client = self._acp_client

        async def _run_prompt():
            try:
                await self._conn.prompt(
                    prompt=content_blocks_from_text(text),
                    session_id=session_id,
                )
            except Exception as exc:
                logger.error('ACPClient: prompt failed: %s', exc)
            finally:
                await acp_client._chunks.put(None)

        asyncio.create_task(_run_prompt())
        async for chunk in self._acp_client.drain():
            yield chunk

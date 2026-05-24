from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click


def _cyan(s: str) -> str:  return f"\033[1;36m{s}\033[0m"
def _blue(s: str) -> str:  return f"\033[1;34m{s}\033[0m"


@click.group('acp')
def acp() -> None:
    """ACP (Agent Client Protocol) transport commands."""


# ── serve ─────────────────────────────────────────────────────────────────────

@acp.command('serve')
@click.option('--cwd', default=None, type=click.Path(exists=True, file_okay=False), help='Working directory')
@click.option('--model', default=None, help='Model ID (e.g. claude-sonnet-4-6)')
@click.option('--provider', default=None, help='Provider override')
@click.option(
    '--sandbox',
    type=click.Choice(['off', 'warn', 'enforce', 'strict'], case_sensitive=False),
    default='off', show_default=True,
    help='Sandbox mode (strict/enforce/warn/off)',
)
def serve(cwd: str | None, model: str | None, provider: str | None, sandbox: str) -> None:
    """
    Run the Operator ACP server over stdio.

    Intended for IDE and CLI integrations (Zed, Claude Code, Codex CLI)
    and for same-machine inter-agent communication.  All logging is
    redirected to stderr; stdout is used exclusively for ACP framing.
    """
    import logging
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

    try:
        asyncio.run(_run_serve(cwd=cwd, model_id=model, provider=provider, sandbox=sandbox))
    except KeyboardInterrupt:
        pass


async def _run_serve(cwd: str | None, model_id: str | None, provider: str | None, sandbox: str = 'off') -> None:
    from program.runtime import Runtime, RuntimeConfig
    from program.acp.transport.stdio import serve_stdio

    cwd_path = Path(cwd).resolve() if cwd else Path.cwd()
    config = RuntimeConfig(
        cwd=cwd_path,
        model_id=model_id or 'claude-sonnet-4-6',
        provider=provider,
        sandbox=sandbox if sandbox != 'off' else None,
    )
    runtime = await Runtime.create(config)
    await serve_stdio(runtime)


# ── serve-http ────────────────────────────────────────────────────────────────

@acp.command('serve-http')
@click.option('--host', default='0.0.0.0', show_default=True, help='Bind address')
@click.option('--port', default=8080, show_default=True, help='Listen port')
@click.option('--cwd', default=None, type=click.Path(exists=True, file_okay=False), help='Working directory')
@click.option('--model', default=None, help='Model ID (e.g. claude-sonnet-4-6)')
@click.option('--provider', default=None, help='Provider override')
def serve_http(host: str, port: int, cwd: str | None, model: str | None, provider: str | None) -> None:
    """
    Run the Operator ACP server over HTTP so remote machines can connect.

    Remote clients authenticate via the device flow:
      1. POST /acp/auth/device  → get user_code
      2. POST /acp/auth/approve → owner approves (run on the server machine)
      3. POST /acp/auth/token   → client polls until it receives the Bearer token
      4. GET  /acp/events       → open SSE stream with Authorization: Bearer <token>
    """
    import logging
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

    click.echo(f"ACP HTTP server starting on http://{host}:{port}")
    try:
        asyncio.run(_run_serve_http(host=host, port=port, cwd=cwd, model_id=model, provider=provider))
    except KeyboardInterrupt:
        pass


async def _run_serve_http(
    host: str,
    port: int,
    cwd: str | None,
    model_id: str | None,
    provider: str | None,
) -> None:
    from program.runtime import Runtime, RuntimeConfig
    from program.acp.transport.http import serve_http as _serve_http

    cwd_path = Path(cwd).resolve() if cwd else Path.cwd()
    config = RuntimeConfig(
        cwd=cwd_path,
        model_id=model_id or 'claude-sonnet-4-6',
        provider=provider,
    )
    runtime = await Runtime.create(config)
    await _serve_http(runtime, host=host, port=port)


# ── serve-webrtc ──────────────────────────────────────────────────────────────

@acp.command('serve-webrtc')
@click.argument('room')
@click.option('--cwd', default=None, type=click.Path(exists=True, file_okay=False), help='Working directory')
@click.option('--model', default=None, help='Model ID (e.g. claude-sonnet-4-6)')
@click.option('--provider', default=None, help='Provider override')
@click.option('--signal-url', default=None, help='PeerJS-compatible signaling URL')
def serve_webrtc(
    room: str,
    cwd: str | None,
    model: str | None,
    provider: str | None,
    signal_url: str | None,
) -> None:
    """
    Run the Operator ACP server over WebRTC for remote machine-to-machine use.

    ROOM is the shared rendezvous name used by the remote client:
      operator acp connect webrtc:<room>
    """
    import logging
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

    click.echo(f"ACP WebRTC server waiting in room {room!r}")
    try:
        asyncio.run(_run_serve_webrtc(room=room, cwd=cwd, model_id=model, provider=provider, signal_url=signal_url))
    except KeyboardInterrupt:
        pass


async def _run_serve_webrtc(
    room: str,
    cwd: str | None,
    model_id: str | None,
    provider: str | None,
    signal_url: str | None,
) -> None:
    from program.runtime import Runtime, RuntimeConfig
    from program.acp.transport.webrtc import DEFAULT_SIGNAL_URL, serve_webrtc as _serve_webrtc

    cwd_path = Path(cwd).resolve() if cwd else Path.cwd()
    config = RuntimeConfig(
        cwd=cwd_path,
        model_id=model_id or 'claude-sonnet-4-6',
        provider=provider,
    )
    runtime = await Runtime.create(config)
    await _serve_webrtc(runtime, room=room, signal_url=signal_url or DEFAULT_SIGNAL_URL)


# ── connect ───────────────────────────────────────────────────────────────────

@acp.command('connect')
@click.argument('target')
@click.argument('extra', nargs=-1)
def connect(target: str, extra: tuple[str, ...]) -> None:
    """
    Connect to an ACP agent as an interactive client.

    \b
    TARGET forms:
      <agent_id>           Resolve from settings.json acp.agents
      stdio:<cmd> [args]   Spawn a subprocess and connect over stdio
      http://<url>         Connect to a remote HTTP agent
      webrtc:<room>        Connect to a remote WebRTC room
    """
    try:
        asyncio.run(_run_connect(target=target, extra=list(extra)))
    except KeyboardInterrupt:
        pass


async def _run_connect(target: str, extra: list[str]) -> None:
    from program.acp.client import ACPClient

    if target.startswith('http://') or target.startswith('https://'):
        client = ACPClient.http(target)
    elif target.startswith('webrtc:'):
        client = ACPClient.webrtc(target[len('webrtc:'):])
    elif target.startswith('stdio:'):
        command = target[len('stdio:'):]
        client = ACPClient.stdio(command, *extra)
    else:
        client = ACPClient.discover(target)

    click.echo(f"{_cyan('[ACP]')} Connecting to {target!r} …")

    async with client:
        async with client.session(cwd=os.getcwd()) as session_id:
            click.echo(f"{_cyan('[ACP]')} Connected  (session {session_id})")
            click.echo("Type your prompt and press Enter. Ctrl-C or /quit to exit.\n")

            while True:
                try:
                    user_input = input(_cyan('\n[You] ')).strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break

                if not user_input:
                    continue
                if user_input in ('/quit', '/exit', '/q'):
                    break

                print(f"{_blue('[Agent]')} ", end='', flush=True)
                async for chunk in client.run_stream(user_input, session_id):
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                print()

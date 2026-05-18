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
def serve(cwd: str | None, model: str | None, provider: str | None) -> None:
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
        asyncio.run(_run_serve(cwd=cwd, model_id=model, provider=provider))
    except KeyboardInterrupt:
        pass


async def _run_serve(cwd: str | None, model_id: str | None, provider: str | None) -> None:
    from program.runtime import Runtime, RuntimeConfig
    from program.acp.stdio import serve_stdio

    config = RuntimeConfig(
        cwd=Path(cwd).resolve() if cwd else Path.cwd(),
        model_id=model_id or 'claude-sonnet-4-6',
        provider=provider,
    )
    runtime = await Runtime.create(config)
    await serve_stdio(runtime)


# ── connect ───────────────────────────────────────────────────────────────────

@acp.command('connect')
@click.argument('target')
@click.argument('extra', nargs=-1)
def connect(target: str, extra: tuple[str, ...]) -> None:
    """
    Connect to an ACP agent as an interactive client.

    \b
    TARGET forms:
      <agent_id>           Auto-discover via ACPRegistry
      stdio:<cmd> [args]   Spawn a subprocess and connect over stdio
      http://<url>         Connect to a remote HTTP agent
    """
    try:
        asyncio.run(_run_connect(target=target, extra=list(extra)))
    except KeyboardInterrupt:
        pass


async def _run_connect(target: str, extra: list[str]) -> None:
    from program.acp.client import ACPClient

    if target.startswith('http://') or target.startswith('https://'):
        client = ACPClient.http(target)
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

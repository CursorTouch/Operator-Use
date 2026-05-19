from __future__ import annotations

import asyncio
import signal
from pathlib import Path

import click

from program.console.repl import repl
from program.console.acp import acp


async def _run_gateway(cwd: Path, model_id: str, provider: str | None) -> None:
    from program.runtime import Runtime, RuntimeConfig
    config = RuntimeConfig(
        cwd=cwd,
        model_id=model_id,
        provider=provider,
    )
    runtime = await Runtime.create(config)
    await asyncio.sleep(0.5)  # let channel tasks register before printing
    channel_ids = list(runtime.gateway_manager.gateway._channels.keys()) if runtime.gateway_manager else []
    channels_str = ', '.join(channel_ids) if channel_ids else 'none'
    click.echo(f"Agent running in {cwd}  (model: {config.model_id})")
    click.echo(f"Channels: {channels_str}")
    click.echo("Press Ctrl-C to stop.\n")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, stop.set)
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    try:
        await stop.wait()
    finally:
        loop.remove_signal_handler(signal.SIGINT)
        loop.remove_signal_handler(signal.SIGTERM)
    click.echo("\nShutting down...")
    await runtime.ashutdown()


@click.group(invoke_without_command=True)
@click.pass_context
@click.option('--cwd', default=None, type=click.Path(exists=True, file_okay=False), help='Working directory')
@click.option('--model', default=None, help='Model ID (e.g. claude-sonnet-4-6)')
@click.option('--provider', default=None, help='Provider override')
@click.option('--repl', 'use_repl', is_flag=True, default=False, help='Start interactive REPL')
def cli(ctx: click.Context, cwd: str | None, model: str | None, provider: str | None, use_repl: bool) -> None:
    """Operator — AI agent harness."""
    from dotenv import load_dotenv
    load_dotenv()

    ctx.ensure_object(dict)
    ctx.obj['cwd'] = cwd
    ctx.obj['model'] = model
    ctx.obj['provider'] = provider

    if ctx.invoked_subcommand is None:
        cwd_path = Path(cwd).resolve() if cwd else Path.cwd()
        if use_repl:
            ctx.invoke(repl, cwd=cwd, model=model, provider=provider)
        else:
            try:
                asyncio.run(_run_gateway(cwd=cwd_path, model_id=model or 'claude-sonnet-4-6', provider=provider))
            except KeyboardInterrupt:
                pass


cli.add_command(repl)
cli.add_command(acp)

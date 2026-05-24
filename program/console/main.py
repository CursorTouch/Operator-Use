from __future__ import annotations

import asyncio
import signal
from pathlib import Path

import click

from program.console.repl import repl
from program.console.acp import acp
from program.console.auth import auth


async def _run_gateway(cwd: Path, model_id: str | None, provider: str | None, resume: bool = False, system_prompt: str | None = None) -> None:
    from program.runtime import Runtime, RuntimeConfig
    from program.gateway.manager import GatewayManager
    config = RuntimeConfig(
        cwd=cwd,
        model_id=model_id or 'claude-sonnet-4-6',
        provider=provider,
        resume=resume,
        system_prompt=system_prompt,
    )
    runtime = await Runtime.create(config)
    gateway_manager = GatewayManager(runtime)
    gateway_manager.start()
    await asyncio.sleep(0.5)  # let channel tasks register before printing
    channel_ids = list(gateway_manager.gateway._channels.keys())
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
    await gateway_manager.astop()
    await runtime.ashutdown()


@click.group(invoke_without_command=True)
@click.pass_context
@click.option('--cwd', default=None, type=click.Path(exists=True, file_okay=False), help='Working directory')
@click.option('--model', default=None, help='Model ID (runtime override)')
@click.option('--provider', default=None, help='Provider override')
@click.option('--repl', 'use_repl', is_flag=True, default=False, help='Start interactive REPL')
@click.option('--resume', is_flag=True, default=False, help='Resume the most recent session instead of starting fresh')
@click.option('--system-prompt', default=None, help='Override the default system prompt')
def cli(ctx: click.Context, cwd: str | None, model: str | None, provider: str | None, use_repl: bool, resume: bool, system_prompt: str | None) -> None:
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
            ctx.invoke(repl, cwd=cwd, model=model, provider=provider, resume=resume, system_prompt=system_prompt)
        else:
            try:
                asyncio.run(_run_gateway(cwd=cwd_path, model_id=model, provider=provider, resume=resume, system_prompt=system_prompt))
            except KeyboardInterrupt:
                pass


@click.command('set')
@click.option('--cwd', default=None, type=click.Path(exists=True, file_okay=False), help='Working directory')
@click.option('--model', default=None, help='Set default model ID')
@click.option('--provider', default=None, help='Set default provider ID')
def set_defaults(cwd: str | None, model: str | None, provider: str | None) -> None:
    """Set default model/provider in settings.json."""
    if model is None and provider is None:
        raise click.UsageError("Pass --model, --provider, or both.")
    from program.settings.manager import SettingsManager

    cwd_path = Path(cwd).resolve() if cwd else Path.cwd()
    settings = SettingsManager.create(cwd_path)
    settings.set_default_model_settings(model=model, provider=provider)
    asyncio.run(settings.flush())
    changed = []
    if model is not None:
        changed.append(f"default_model={model}")
    if provider is not None:
        changed.append(f"default_provider={provider}")
    click.echo("Updated settings.json: " + ", ".join(changed))


@click.command('unset')
@click.option('--cwd', default=None, type=click.Path(exists=True, file_okay=False), help='Working directory')
@click.option('--model', 'unset_model', is_flag=True, default=False, help='Unset default model')
@click.option('--provider', 'unset_provider', is_flag=True, default=False, help='Unset default provider')
def unset_defaults(cwd: str | None, unset_model: bool, unset_provider: bool) -> None:
    """Unset default model/provider in settings.json."""
    if not unset_model and not unset_provider:
        raise click.UsageError("Pass --model, --provider, or both.")
    from program.settings.manager import SettingsManager

    cwd_path = Path(cwd).resolve() if cwd else Path.cwd()
    settings = SettingsManager.create(cwd_path)
    settings.unset_default_model_settings(model=unset_model, provider=unset_provider)
    asyncio.run(settings.flush())
    changed = []
    if unset_model:
        changed.append("default_model")
    if unset_provider:
        changed.append("default_provider")
    click.echo("Unset settings.json: " + ", ".join(changed))


cli.add_command(repl)
cli.add_command(acp)
cli.add_command(auth)
cli.add_command(set_defaults)
cli.add_command(unset_defaults)

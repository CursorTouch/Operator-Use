import asyncio
from pathlib import Path

import click

from operator_use.console.repl import repl
from operator_use.console.acp import acp
from operator_use.console.auth import auth
from operator_use.console.gateway import GatewayOptions, gateway, run_gateway_foreground


@click.group(invoke_without_command=True)
@click.pass_context
@click.option('--cwd', default=None, type=click.Path(exists=True, file_okay=False), help='Working directory')
@click.option('--model', default=None, help='Model ID (runtime override)')
@click.option('--provider', default=None, help='Provider override')
@click.option('--repl', 'use_repl', is_flag=True, default=False, help='Start interactive REPL')
@click.option('--resume', is_flag=True, default=False, help='Resume the most recent session instead of starting fresh')
@click.option('--system-prompt', default=None, help='Override the default system prompt')
@click.option('--prompt', default=None, help='Inject an initial message so the agent starts immediately.')
@click.option('--session-file', default=None, hidden=True, help='Open a specific session file.')
def cli(ctx: click.Context, cwd: str | None, model: str | None, provider: str | None, use_repl: bool, resume: bool, system_prompt: str | None, prompt: str | None, session_file: str | None) -> None:
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
            ctx.invoke(repl, cwd=cwd, model=model, provider=provider, resume=resume, system_prompt=system_prompt, prompt=prompt, session_file=session_file)
        else:
            try:
                asyncio.run(run_gateway_foreground(GatewayOptions(
                    cwd=cwd_path,
                    model=model,
                    provider=provider,
                    resume=resume,
                    system_prompt=system_prompt,
                    prompt=prompt,
                    session_file=Path(session_file) if session_file else None,
                )))
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
    from operator_use.settings.manager import SettingsManager

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
    from operator_use.settings.manager import SettingsManager

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
cli.add_command(gateway)
cli.add_command(set_defaults)
cli.add_command(unset_defaults)


if __name__ == "__main__":
    cli()

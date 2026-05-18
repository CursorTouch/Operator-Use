from __future__ import annotations

import click

from program.console.repl import repl
from program.console.acp import acp


@click.group(invoke_without_command=True)
@click.pass_context
@click.option('--cwd', default=None, type=click.Path(exists=True, file_okay=False), help='Working directory')
@click.option('--model', default=None, help='Model ID (e.g. claude-sonnet-4-6)')
@click.option('--provider', default=None, help='Provider override')
def cli(ctx: click.Context, cwd: str | None, model: str | None, provider: str | None) -> None:
    """Operator — AI agent harness."""
    ctx.ensure_object(dict)
    ctx.obj['cwd'] = cwd
    ctx.obj['model'] = model
    ctx.obj['provider'] = provider
    if ctx.invoked_subcommand is None:
        ctx.invoke(repl, cwd=cwd, model=model, provider=provider)


cli.add_command(repl)
cli.add_command(acp)

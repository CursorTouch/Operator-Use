from __future__ import annotations

import asyncio
import os
import sys
import webbrowser

import click


async def _prompt(message: str) -> str:
    return await asyncio.to_thread(input, message)


async def _read_line_cancelable(message: str = "") -> str:
    if message:
        sys.stdout.write(message)
        sys.stdout.flush()
    loop = asyncio.get_running_loop()
    dup_fd = os.dup(sys.stdin.fileno())
    pipe = os.fdopen(dup_fd, "r")
    reader = asyncio.StreamReader()
    transport, _ = await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), pipe
    )
    try:
        data = await reader.readline()
        return data.decode(errors="replace").strip()
    finally:
        transport.close()
        try:
            pipe.close()
        except Exception:
            pass


async def _do_login(provider_id: str | None) -> None:
    from program.inference.api.text.service import LLM
    from program.inference.provider.oauth.types import OAuthLoginCallbacks, OAuthAuthInfo, OAuthPrompt

    auth = LLM._auth_store
    reg = LLM._providers
    oauth_providers = reg.get_oauth_providers()

    if not oauth_providers:
        click.echo("No OAuth providers available.")
        return

    if provider_id is None:
        click.echo("Usage: operator auth login <provider-id>")
        click.echo("\nAvailable OAuth providers:")
        for p in oauth_providers:
            status = auth.get_auth_status(p.id)
            tag = " [logged in]" if status.configured else ""
            click.echo(f"  {p.id}{tag}")
        return

    provider = reg.get_oauth_provider(provider_id)
    if provider is None:
        click.echo(f"Unknown OAuth provider: '{provider_id}'.")
        return

    def on_auth(info: OAuthAuthInfo) -> None:
        click.echo(f"\n  Open this URL to authenticate:")
        click.echo(f"  {info.url}")
        if info.instructions:
            click.echo(f"  {info.instructions}")
        webbrowser.open(info.url)

    async def on_prompt(prompt: OAuthPrompt) -> str:
        suffix = " (leave blank to skip)" if prompt.allow_empty else ""
        placeholder = f" [{prompt.placeholder}]" if prompt.placeholder else ""
        return await _prompt(f"  {prompt.message}{placeholder}{suffix} ")

    def on_progress(msg: str) -> None:
        click.echo(f"  {msg}")

    async def on_manual_code_input() -> str:
        return await _read_line_cancelable(
            "  Paste the redirect URL or authorization code: "
        )

    callbacks = OAuthLoginCallbacks(
        on_auth=on_auth,
        on_prompt=on_prompt,
        on_progress=on_progress,
        on_manual_code_input=on_manual_code_input,
    )

    click.echo(f"Logging in to {provider.name}...")
    try:
        await auth.login(provider_id, callbacks)
        click.echo(f"Logged in to {provider.name}.")
    except Exception as e:
        click.echo(f"Login failed: {e}")


async def _do_logout(provider_id: str | None) -> None:
    from program.inference.api.text.service import LLM

    auth = LLM._auth_store
    reg = LLM._providers

    if provider_id is None:
        click.echo("Usage: operator auth logout <provider-id>")
        logged_in = [p for p in reg.get_oauth_providers() if auth.has(p.id)]
        if logged_in:
            click.echo("\nLogged in providers:")
            for p in logged_in:
                click.echo(f"  {p.id}")
        return

    if not LLM._auth_store.has(provider_id):
        click.echo(f"Not logged in to '{provider_id}'.")
        return

    provider = LLM._providers.get_oauth_provider(provider_id)
    name = provider.name if provider else provider_id
    try:
        await LLM._auth_store.logout(provider_id)
        click.echo(f"Logged out from {name}.")
    except Exception as e:
        click.echo(f"Logout failed: {e}")


def _do_status() -> None:
    from program.inference.api.text.service import LLM

    auth = LLM._auth_store
    reg = LLM._providers

    click.echo("Auth status:")
    for provider in reg.get_oauth_providers():
        status = auth.get_auth_status(provider.id)
        if status.configured:
            label = f" via {status.label}" if status.label else ""
            click.echo(f"  {provider.name:<35} logged in ({status.source}{label})")
        else:
            click.echo(f"  {provider.name:<35} not logged in")

    api_providers = reg.get_api_providers()
    if api_providers:
        click.echo()
        for provider in api_providers:
            status = auth.get_auth_status(provider.id)
            if status.configured:
                label = f" via {status.label}" if status.label else ""
                click.echo(f"  {provider.name:<35} api key set ({status.source}{label})")
            else:
                click.echo(f"  {provider.name:<35} no api key")


class _AuthGroup(click.Group):
    """Route `operator auth <provider-id>` as a shortcut for `operator auth login <provider-id>`."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if args and args[0] not in self.commands and not args[0].startswith('-'):
            args = ['login'] + args
        return super().parse_args(ctx, args)


@click.group(cls=_AuthGroup, invoke_without_command=True)
@click.pass_context
def auth(ctx: click.Context) -> None:
    """Manage OAuth provider authentication."""
    if ctx.invoked_subcommand is None:
        _do_status()


@auth.command('login')
@click.argument('provider_id', required=False, default=None)
def login(provider_id: str | None) -> None:
    """Log in to an OAuth provider."""
    asyncio.run(_do_login(provider_id))


@auth.command('logout')
@click.argument('provider_id', required=False, default=None)
def logout(provider_id: str | None) -> None:
    """Log out from an OAuth provider."""
    asyncio.run(_do_logout(provider_id))


@auth.command('status')
def status() -> None:
    """Show authentication status for all providers."""
    _do_status()

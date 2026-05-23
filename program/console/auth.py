from __future__ import annotations

import asyncio
import os
import sys
import webbrowser

import click


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Core logic helpers
# ---------------------------------------------------------------------------

def _print_all_providers(auth, reg) -> None:
    """Print auth status for every registered provider."""
    oauth_providers = reg.get_oauth_providers()
    api_providers = reg.get_api_providers()

    if oauth_providers:
        click.echo("OAuth providers:")
        for p in oauth_providers:
            status = auth.get_auth_status(p.id)
            if status.configured:
                label = f" via {status.label}" if status.label else ""
                click.echo(f"  {p.id:<30} {p.name:<30} logged in ({status.source}{label})")
            else:
                click.echo(f"  {p.id:<30} {p.name:<30} not logged in")

    if api_providers:
        if oauth_providers:
            click.echo()
        click.echo("API-key providers:")
        for p in api_providers:
            status = auth.get_auth_status(p.id)
            if status.configured:
                label = f" via {status.label}" if status.label else ""
                click.echo(f"  {p.id:<30} {p.name:<30} api key set ({status.source}{label})")
            else:
                click.echo(f"  {p.id:<30} {p.name:<30} no api key")

    if not oauth_providers and not api_providers:
        click.echo("No providers registered.")


def _print_provider(provider_id: str, auth, reg) -> None:
    """Print detailed auth status for a single provider."""
    from program.inference.provider.types import OAuthProvider, APIProvider

    provider = reg.get(provider_id)
    if provider is None:
        click.echo(f"Unknown provider: '{provider_id}'.")
        click.echo("Use 'operator auth list' to see all available providers.")
        return

    status = auth.get_auth_status(provider_id)
    kind = "OAuth" if isinstance(provider, OAuthProvider) else "API-key"

    click.echo(f"Provider : {provider.name} ({provider_id})")
    click.echo(f"Type     : {kind}")
    if status.configured:
        label = f" via {status.label}" if status.label else ""
        click.echo(f"Status   : authenticated ({status.source}{label})")
    else:
        click.echo(f"Status   : not authenticated")


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------

@click.group(invoke_without_command=True)
@click.pass_context
def auth(ctx: click.Context) -> None:
    """Manage provider authentication."""
    if ctx.invoked_subcommand is None:
        from program.inference.api.text.service import LLM
        _print_all_providers(LLM._auth_store, LLM._providers)


# ---------------------------------------------------------------------------
# auth list [provider]
# ---------------------------------------------------------------------------

@auth.command("list")
@click.argument("provider", required=False, default=None)
def auth_list(provider: str | None) -> None:
    """Show all providers, or details for a specific PROVIDER."""
    from program.inference.api.text.service import LLM

    auth_store = LLM._auth_store
    reg = LLM._providers

    if provider:
        _print_provider(provider, auth_store, reg)
    else:
        _print_all_providers(auth_store, reg)


# ---------------------------------------------------------------------------
# auth set <provider> --api-key <key> | --oauth
# ---------------------------------------------------------------------------

@auth.command("set")
@click.argument("provider")
@click.option("--api-key", "api_key", default=None, help="API key to store for the provider.")
@click.option("--oauth", "use_oauth", is_flag=True, default=False, help="Log in via OAuth flow.")
def auth_set(provider: str, api_key: str | None, use_oauth: bool) -> None:
    """Set credentials for PROVIDER.

    \b
    Examples:
      operator auth set openai --api-key sk-...
      operator auth set claude --oauth
    """
    from program.inference.api.text.service import LLM

    auth_store = LLM._auth_store
    reg = LLM._providers

    p = reg.get(provider)
    if p is None:
        click.echo(f"Unknown provider: '{provider}'.")
        click.echo("Use 'operator auth list' to see all available providers.")
        return

    if api_key is not None:
        from program.auth.types import APICredential
        auth_store.set(provider, APICredential(key=api_key))
        click.echo(f"API key set for {p.name} ({provider}).")
        return

    if use_oauth:
        from program.inference.provider.types import OAuthProvider
        from program.inference.provider.oauth.types import OAuthLoginCallbacks, OAuthAuthInfo, OAuthPrompt

        if not isinstance(p, OAuthProvider):
            click.echo(f"Provider '{provider}' does not support OAuth. Use --api-key instead.")
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

        click.echo(f"Logging in to {p.name}...")
        try:
            asyncio.run(auth_store.login(provider, callbacks))
            click.echo(f"Logged in to {p.name}.")
        except Exception as e:
            click.echo(f"Login failed: {e}")
        return

    # Neither flag provided
    click.echo("Specify --api-key <key> or --oauth.")
    click.echo(f"  operator auth set {provider} --api-key <key>")
    click.echo(f"  operator auth set {provider} --oauth")


# ---------------------------------------------------------------------------
# auth unset <provider>
# ---------------------------------------------------------------------------

@auth.command("unset")
@click.argument("provider")
def auth_unset(provider: str) -> None:
    """Remove stored credentials for PROVIDER (API key or OAuth session)."""
    from program.inference.api.text.service import LLM
    from program.inference.provider.types import OAuthProvider

    auth_store = LLM._auth_store
    reg = LLM._providers

    p = reg.get(provider)
    name = p.name if p else provider

    if not auth_store.has(provider):
        click.echo(f"No credentials stored for '{name}'.")
        return

    if p and isinstance(p, OAuthProvider):
        try:
            asyncio.run(auth_store.logout(provider))
            click.echo(f"Logged out from {name}.")
        except Exception as e:
            click.echo(f"Logout failed: {e}")
    else:
        auth_store.remove(provider)
        click.echo(f"API key removed for {name}.")


# ---------------------------------------------------------------------------
# auth status <provider>
# ---------------------------------------------------------------------------

@auth.command("status")
@click.argument("provider")
def auth_status(provider: str) -> None:
    """Show the auth status of a specific PROVIDER."""
    from program.inference.api.text.service import LLM

    _print_provider(provider, LLM._auth_store, LLM._providers)

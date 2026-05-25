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

class AuthGroup(click.Group):
    def get_command(self, ctx: click.Context, cmd_name: str):
        command = super().get_command(ctx, cmd_name)
        if command is not None:
            return command
        return _provider_auth_command(cmd_name)

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


def _select_oauth_provider(provider: str | None, auth_store, reg) -> str | None:
    oauth_providers = reg.get_oauth_providers()
    if not oauth_providers:
        click.echo("No OAuth providers available.")
        return None

    if provider:
        if reg.get_oauth_provider(provider) is None:
            click.echo(f"Unknown OAuth provider: '{provider}'.")
            click.echo("Use 'operator auth list' to see all available providers.")
            return None
        return provider

    click.echo("Available OAuth providers:")
    for i, p in enumerate(oauth_providers, 1):
        status = auth_store.get_auth_status(p.id)
        tag = " [logged in]" if status.configured else ""
        click.echo(f"  {i}. {p.name}  ({p.id}){tag}")
    choice = click.prompt("Enter number or provider id", type=str).strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(oauth_providers):
            return oauth_providers[idx].id
        click.echo("Invalid choice.")
        return None
    except ValueError:
        if reg.get_oauth_provider(choice) is None:
            click.echo(f"Unknown OAuth provider: '{choice}'.")
            return None
        return choice


def _oauth_callbacks():
    from program.inference.provider.oauth.types import OAuthLoginCallbacks, OAuthAuthInfo, OAuthPrompt

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

    return OAuthLoginCallbacks(
        on_auth=on_auth,
        on_prompt=on_prompt,
        on_progress=on_progress,
        on_manual_code_input=on_manual_code_input,
    )


def _login_oauth_provider(provider: str | None) -> None:
    from program.inference.api.text.service import LLM

    auth_store = LLM._auth_store
    reg = LLM._providers
    provider_id = _select_oauth_provider(provider, auth_store, reg)
    if provider_id is None:
        return

    p = reg.get_oauth_provider(provider_id)
    if p is None:
        click.echo(f"Unknown OAuth provider: '{provider_id}'.")
        return

    click.echo(f"Logging in to {p.name}...")
    try:
        asyncio.run(auth_store.login(provider_id, _oauth_callbacks()))
        click.echo(f"Logged in to {p.name}.")
    except Exception as e:
        click.echo(f"Login failed: {e}")


def _logout_oauth_provider(provider: str | None) -> None:
    from program.inference.api.text.service import LLM

    auth_store = LLM._auth_store
    reg = LLM._providers

    if provider is None:
        logged_in = [p for p in reg.get_oauth_providers() if auth_store.has(p.id)]
        if not logged_in:
            click.echo("Not logged in to any OAuth providers.")
            return
        click.echo("Logged in OAuth providers:")
        for i, p in enumerate(logged_in, 1):
            click.echo(f"  {i}. {p.name}  ({p.id})")
        choice = click.prompt("Enter number or provider id", type=str).strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(logged_in):
                provider = logged_in[idx].id
            else:
                click.echo("Invalid choice.")
                return
        except ValueError:
            provider = choice

    if reg.get_oauth_provider(provider) is None:
        click.echo(f"Unknown OAuth provider: '{provider}'.")
        click.echo("Use 'operator auth list' to see all available providers.")
        return

    if not auth_store.has(provider):
        click.echo(f"Not logged in to '{provider}'.")
        return

    p = reg.get_oauth_provider(provider)
    name = p.name if p else provider
    try:
        asyncio.run(auth_store.logout(provider))
        click.echo(f"Logged out from {name}.")
    except Exception as e:
        click.echo(f"Logout failed: {e}")


def _provider_auth_command(provider: str) -> click.Command:
    @click.command(
        name=provider,
        context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
    )
    @click.argument("tokens", nargs=-1, type=click.UNPROCESSED)
    def _command(tokens: tuple[str, ...]) -> None:
        _handle_provider_auth(provider, list(tokens))

    return _command


def _pop_api_key(tokens: list[str]) -> tuple[bool, str | None, list[str]]:
    api_key_requested = False
    api_key: str | None = None
    rest: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--api-key":
            api_key_requested = True
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                api_key = tokens[i + 1]
                i += 2
            else:
                i += 1
            continue
        if token.startswith("--api-key="):
            api_key_requested = True
            api_key = token.split("=", 1)[1]
            i += 1
            continue
        rest.append(token)
        i += 1
    return api_key_requested, api_key, rest


def _handle_provider_auth(provider: str, tokens: list[str]) -> None:
    from program.auth.types import APICredential
    from program.inference.api.text.service import LLM
    from program.inference.provider.types import APIProvider, OAuthProvider

    auth_store = LLM._auth_store
    reg = LLM._providers
    p = reg.get(provider)
    if p is None:
        click.echo(f"Unknown provider: '{provider}'.")
        click.echo("Use 'operator auth list' to see all available providers.")
        return

    action = None
    if tokens and tokens[0] in {"set", "unset", "status"}:
        action = tokens.pop(0)

    api_key_requested, api_key, rest = _pop_api_key(tokens)
    if rest:
        click.echo(f"Unexpected argument(s): {' '.join(rest)}")
        _print_provider_auth_usage(provider)
        return

    if isinstance(p, OAuthProvider):
        if action == "unset":
            _logout_oauth_provider(provider)
        elif action == "set" or api_key_requested:
            click.echo(f"Provider '{provider}' uses OAuth. Use 'operator auth login {provider}'.")
        else:
            _print_provider(provider, auth_store, reg)
        return

    if not isinstance(p, APIProvider):
        _print_provider(provider, auth_store, reg)
        return

    if action == "unset":
        if auth_store.has(provider):
            auth_store.remove(provider)
        auth_store.remove_runtime_api_key(provider)
        click.echo(f"API key cleared for {p.name} ({provider}).")
        return

    if action == "status" or (action is None and not api_key_requested):
        _print_provider(provider, auth_store, reg)
        return

    if action == "set":
        if not api_key_requested:
            click.echo("Pass --api-key to save an API key.")
            click.echo(f"  operator auth {provider} set --api-key <key>")
            return
        if api_key is None:
            api_key = click.prompt("API key", hide_input=True)
        auth_store.set(provider, APICredential(key=api_key))
        click.echo(f"API key saved for {p.name} ({provider}).")
        return

    if api_key_requested:
        if api_key is None:
            api_key = click.prompt("API key", hide_input=True)
        auth_store.set_runtime_api_key(provider, api_key)
        click.echo(f"Runtime API key override set for {p.name} ({provider}).")
        return

    _print_provider_auth_usage(provider)


def _print_provider_auth_usage(provider: str) -> None:
    click.echo("Usage:")
    click.echo(f"  operator auth {provider}")
    click.echo(f"  operator auth {provider} --api-key <key>")
    click.echo(f"  operator auth {provider} set --api-key <key>")
    click.echo(f"  operator auth {provider} unset")


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------

@click.group(cls=AuthGroup, invoke_without_command=True)
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
# auth login [provider]
# ---------------------------------------------------------------------------

@auth.command("login")
@click.argument("provider", required=False, default=None)
def auth_login(provider: str | None) -> None:
    """Log in to an OAuth PROVIDER."""
    _login_oauth_provider(provider)


# ---------------------------------------------------------------------------
# auth logout [provider]
# ---------------------------------------------------------------------------

@auth.command("logout")
@click.argument("provider", required=False, default=None)
def auth_logout(provider: str | None) -> None:
    """Log out from an OAuth PROVIDER."""
    _logout_oauth_provider(provider)


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

        if not isinstance(p, OAuthProvider):
            click.echo(f"Provider '{provider}' does not support OAuth. Use --api-key instead.")
            return
        _login_oauth_provider(provider)
        return

    # Neither flag provided
    click.echo("Specify --api-key <key> or --oauth.")
    click.echo(f"  operator auth set {provider} --api-key <key>")
    click.echo(f"  operator auth login {provider}")


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
        _logout_oauth_provider(provider)
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

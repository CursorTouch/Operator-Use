from __future__ import annotations

import asyncio
import os
import sys
import webbrowser

from operator_use.commands.types import SlashCommandInfo

if TYPE_CHECKING := False:
    from operator_use.commands.registry import CommandRegistry

from typing import TYPE_CHECKING


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


async def _handle_login(registry: CommandRegistry, args: list[str]) -> None:
    from operator_use.inference.api.text.service import LLM
    from operator_use.inference.provider.oauth.types import OAuthLoginCallbacks, OAuthAuthInfo, OAuthPrompt

    auth = LLM._auth_store
    reg = LLM._providers
    oauth_providers = reg.get_oauth_providers()

    if not oauth_providers:
        print("No OAuth providers available.")
        return

    provider_id = args[0] if args else None

    if provider_id is None:
        print("Available OAuth providers:")
        for i, p in enumerate(oauth_providers, 1):
            status = auth.get_auth_status(p.id)
            tag = " [logged in]" if status.configured else ""
            print(f"  {i}. {p.name}  ({p.id}){tag}")
        choice = (await _prompt("Enter number or provider id: ")).strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(oauth_providers):
                provider_id = oauth_providers[idx].id
            else:
                print("Invalid choice.")
                return
        except ValueError:
            provider_id = choice

    provider = reg.get_oauth_provider(provider_id)
    if provider is None:
        print(f"Unknown OAuth provider: '{provider_id}'.")
        return

    def on_auth(info: OAuthAuthInfo) -> None:
        print(f"\n  Open this URL to authenticate:")
        print(f"  {info.url}")
        if info.instructions:
            print(f"  {info.instructions}")
        webbrowser.open(info.url)

    async def on_prompt(prompt: OAuthPrompt) -> str:
        suffix = " (leave blank to skip)" if prompt.allow_empty else ""
        placeholder = f" [{prompt.placeholder}]" if prompt.placeholder else ""
        return await _prompt(f"  {prompt.message}{placeholder}{suffix} ")

    def on_progress(msg: str) -> None:
        print(f"  {msg}")

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

    print(f"Logging in to {provider.name}...")
    try:
        await auth.login(provider_id, callbacks)
        print(f"Logged in to {provider.name}.")
    except Exception as e:
        print(f"Login failed: {e}")


async def _handle_logout(registry: CommandRegistry, args: list[str]) -> None:
    from operator_use.inference.api.text.service import LLM

    auth = LLM._auth_store
    reg = LLM._providers

    provider_id = args[0] if args else None

    if provider_id is None:
        logged_in = [p for p in reg.get_oauth_providers() if auth.has(p.id)]
        if not logged_in:
            print("Not logged in to any OAuth providers.")
            return
        print("Logged in providers:")
        for i, p in enumerate(logged_in, 1):
            print(f"  {i}. {p.name}  ({p.id})")
        choice = (await _prompt("Enter number or provider id: ")).strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(logged_in):
                provider_id = logged_in[idx].id
            else:
                print("Invalid choice.")
                return
        except ValueError:
            provider_id = choice

    if not auth.has(provider_id):
        print(f"Not logged in to '{provider_id}'.")
        return

    provider = reg.get_oauth_provider(provider_id)
    name = provider.name if provider else provider_id
    try:
        await auth.logout(provider_id)
        print(f"Logged out from {name}.")
    except Exception as e:
        print(f"Logout failed: {e}")


async def _handle_auth(registry: CommandRegistry, args: list[str]) -> None:
    from operator_use.inference.api.text.service import LLM

    auth = LLM._auth_store
    reg = LLM._providers

    print("Auth status:")
    for provider in reg.get_oauth_providers():
        status = auth.get_auth_status(provider.id)
        if status.configured:
            label = f" via {status.label}" if status.label else ""
            print(f"  {provider.name:<35} logged in ({status.source}{label})")
        else:
            print(f"  {provider.name:<35} not logged in")

    api_providers = reg.get_api_providers()
    if api_providers:
        print()
        for provider in api_providers:
            status = auth.get_auth_status(provider.id)
            if status.configured:
                label = f" via {status.label}" if status.label else ""
                print(f"  {provider.name:<35} api key set ({status.source}{label})")
            else:
                print(f"  {provider.name:<35} no api key")


commands = [
    SlashCommandInfo(
        name='login',
        description='Log in to an OAuth provider (Claude Pro/Max, GitHub Copilot, ChatGPT Plus, Google). Optionally pass the provider id.',
        handler=_handle_login,
    ),
    SlashCommandInfo(
        name='logout',
        description='Log out from an OAuth provider. Optionally pass the provider id.',
        handler=_handle_logout,
    ),
    SlashCommandInfo(
        name='auth',
        description='Show authentication status for all providers.',
        handler=_handle_auth,
    ),
]

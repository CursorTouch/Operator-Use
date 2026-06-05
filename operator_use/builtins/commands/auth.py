"""auth slash command — Manage credentials and OAuth flows for integrations."""
from __future__ import annotations

from operator_use.commands.types import SlashCommandInfo

if TYPE_CHECKING := False:
    from operator_use.commands.registry import CommandRegistry

from typing import TYPE_CHECKING


async def _handle_auth(registry: CommandRegistry, args: list[str]) -> None:
    """Execute the command with parsed arguments."""
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
        name='auth',
        description='Show authentication status for all providers.',
        handler=_handle_auth,
    ),
]

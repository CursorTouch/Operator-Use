"""MCP Server CRUD operations for CLI setup."""

import json
from typing import Optional
from operator_use.cli.tui import (
    clear_screen, print_start, select, text_input, confirm, console
)


def show_mcp_menu(mcp_servers: dict[str, dict]) -> dict[str, dict]:
    """Interactive MCP server management menu."""
    while True:
        clear_screen()
        print_start("MCP Server Configuration")

        # Show current servers
        if mcp_servers:
            console.print("[bold]Current MCP Servers:[/bold]")
            for i, (name, config) in enumerate(mcp_servers.items(), 1):
                transport = config.get("transport", "stdio")
                status = f"[{transport}]"
                if transport == "stdio":
                    cmd = config.get("command", "?")
                    console.print(f"  {i}. {name:20} {status:12} command={cmd}")
                else:
                    url = config.get("url", "?")
                    console.print(f"  {i}. {name:20} {status:12} url={url}")
        else:
            console.print("[dim]No MCP servers configured[/dim]")

        console.print()
        options = ["Add MCP Server"]
        if mcp_servers:
            options.extend(["Edit MCP Server", "Remove MCP Server"])
        options.append("Done")

        choice = select("MCP Server Management:", options)

        if choice == "Add MCP Server":
            name = add_mcp_server(mcp_servers)
            if name:
                mcp_servers[name] = create_mcp_config(name)
        elif choice == "Edit MCP Server":
            edit_mcp_server(mcp_servers)
        elif choice == "Remove MCP Server":
            remove_mcp_server(mcp_servers)
        elif choice == "Done":
            break

    return mcp_servers


def add_mcp_server(existing: dict) -> Optional[str]:
    """Add a new MCP server."""
    clear_screen()
    print_start("Add MCP Server")

    while True:
        name = text_input(
            "Server name (alphanumeric, lowercase):",
            default=""
        )

        if not name:
            if confirm("Cancel adding server?"):
                return None
            continue

        # Validate name: lowercase alphanumeric with - and _ allowed
        if not name.replace("_", "").replace("-", "").isalnum() or not name.islower():
            console.print("[red]Name must be lowercase alphanumeric (- and _ allowed)[/red]")
            continue

        if name in existing:
            console.print(f"[red]Server '{name}' already exists[/red]")
            continue

        return name


def create_mcp_config(name: str) -> dict:
    """Create MCP server configuration interactively."""
    clear_screen()
    print_start(f"Configure MCP Server: {name}")

    # Choose transport
    transport_choice = select(
        "Transport method:",
        [
            "Stdio (local subprocess)",
            "HTTP/SSE (remote)",
        ]
    )
    transport = "stdio" if "Stdio" in transport_choice else "http"

    config = {"name": name, "transport": transport}

    if transport == "stdio":
        # Stdio configuration
        config["command"] = text_input(
            "Command to run (e.g., npx, uvx, python):",
            default="uvx"
        )

        args_str = text_input(
            "Arguments (comma-separated, e.g., mcp-server-filesystem, /path):",
            default=""
        )
        config["args"] = [arg.strip() for arg in args_str.split(",") if arg.strip()]

        # Optional env vars
        if confirm("Add environment variables?"):
            env = {}
            while True:
                key = text_input("Variable name (or 'done' to finish):", default="")
                if key.lower() == "done":
                    break
                if key:
                    value = text_input(f"Value for {key}:", default="")
                    env[key] = value
            if env:
                config["env"] = env
    else:
        # HTTP configuration
        config["url"] = text_input(
            "Server URL (e.g., http://localhost:3000):",
            default="http://localhost:3000"
        )

        if confirm("Add authentication token?"):
            config["auth_token"] = text_input("Bearer token:", default="")

    # Preview
    clear_screen()
    print_start(f"Review Configuration: {name}")
    console.print("[bold]Configuration Preview:[/bold]")
    for key, value in config.items():
        if isinstance(value, list):
            console.print(f"  {key}: {', '.join(value)}")
        elif isinstance(value, dict):
            console.print(f"  {key}:")
            for k, v in value.items():
                console.print(f"    {k}: {v}")
        else:
            console.print(f"  {key}: {value}")

    if not confirm("Save this configuration?"):
        console.print("[yellow]Configuration cancelled[/yellow]")
        return {}

    return config


def edit_mcp_server(mcp_servers: dict[str, dict]) -> None:
    """Edit an existing MCP server."""
    if not mcp_servers:
        console.print("[red]No MCP servers to edit[/red]")
        return

    clear_screen()
    print_start("Edit MCP Server")

    options = list(mcp_servers.keys())
    name = select("Select server to edit:", options)

    clear_screen()
    print_start(f"Edit MCP Server: {name}")

    current = mcp_servers[name]

    # Edit fields
    edit_options = ["Transport", "Command/URL"]
    if current.get("transport") == "stdio":
        edit_options.extend(["Arguments", "Environment variables"])
    elif current.get("transport") == "http":
        edit_options.append("Auth token")
    edit_options.append("Back")

    edit_choice = select("What to edit?", edit_options)

    if edit_choice == "Back":
        return
    elif edit_choice == "Transport":
        new_transport_choice = select("New transport:", ["Stdio", "HTTP/SSE"])
        new_transport = "stdio" if "Stdio" in new_transport_choice else "http"
        if new_transport != current.get("transport"):
            # Clear old transport-specific fields
            current["transport"] = new_transport
            if new_transport == "stdio":
                current.pop("url", None)
                current.pop("auth_token", None)
                current["command"] = text_input("Command:", default=current.get("command", "uvx"))
                args_str = text_input("Arguments (comma-separated):", default="")
                current["args"] = [arg.strip() for arg in args_str.split(",") if arg.strip()]
            else:
                current.pop("command", None)
                current.pop("args", None)
                current.pop("env", None)
                current["url"] = text_input("URL:", default=current.get("url", "http://localhost:3000"))

    elif edit_choice == "Command/URL":
        if current.get("transport") == "stdio":
            current["command"] = text_input("Command:", default=current.get("command", "uvx"))
        else:
            current["url"] = text_input("URL:", default=current.get("url", ""))

    elif edit_choice == "Arguments":
        args_str = text_input(
            "Arguments (comma-separated):",
            default=", ".join(current.get("args", []))
        )
        current["args"] = [arg.strip() for arg in args_str.split(",") if arg.strip()]

    elif edit_choice == "Environment variables":
        current.pop("env", None)
        if confirm("Add environment variables?"):
            env = {}
            while True:
                key = text_input("Variable name (or 'done'):", default="")
                if key.lower() == "done":
                    break
                if key:
                    value = text_input(f"Value for {key}:", default="")
                    env[key] = value
            if env:
                current["env"] = env

    elif edit_choice == "Auth token":
        current["auth_token"] = text_input("Bearer token:", default=current.get("auth_token", ""))

    console.print("[green]Server configuration updated[/green]")


def remove_mcp_server(mcp_servers: dict[str, dict]) -> None:
    """Remove an MCP server."""
    if not mcp_servers:
        console.print("[red]No MCP servers to remove[/red]")
        return

    clear_screen()
    print_start("Remove MCP Server")

    options = list(mcp_servers.keys())
    name = select("Select server to remove:", options)

    if confirm(f"Really remove MCP server '{name}'?"):
        mcp_servers.pop(name, None)
        console.print(f"[green]Server '{name}' removed[/green]")
    else:
        console.print("[yellow]Removal cancelled[/yellow]")


def validate_mcp_servers(mcp_servers: dict[str, dict]) -> bool:
    """Validate MCP server configurations."""
    for name, config in mcp_servers.items():
        # Check required fields
        if "transport" not in config:
            console.print(f"[red]Server '{name}': missing 'transport'[/red]")
            return False

        transport = config["transport"]
        if transport == "stdio":
            if "command" not in config:
                console.print(f"[red]Server '{name}': stdio requires 'command'[/red]")
                return False
        elif transport in ("http", "sse"):
            if "url" not in config:
                console.print(f"[red]Server '{name}': {transport} requires 'url'[/red]")
                return False
        else:
            console.print(f"[red]Server '{name}': unknown transport '{transport}'[/red]")
            return False

    return True


if __name__ == "__main__":
    # Test
    test_mcp = {
        "filesystem": {
            "name": "filesystem",
            "transport": "stdio",
            "command": "uvx",
            "args": ["mcp-server-filesystem"],
        }
    }

    result = show_mcp_menu(test_mcp)
    console.print("\n[bold]Final config:[/bold]")
    console.print(json.dumps(result, indent=2))

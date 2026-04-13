import os

import asyncio
import json
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()

from operator_use.config.paths import get_userdata_dir

USERDATA_DIR = get_userdata_dir()

app = typer.Typer(
    name="Operator",
    help="Operator CLI",
    invoke_without_command=True,
    no_args_is_help=False,
)


@app.callback()
def default(
    ctx: typer.Context,
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show logs in console", is_eager=False
    ),
):
    if ctx.invoked_subcommand is None:
        gateway(verbose=verbose)


@app.command("help")
def help_cmd(ctx: typer.Context):
    """Show available commands and usage."""
    console.print("\n[bold]Operator[/bold] — your personal AI agent\n")
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column("Command", style="bold cyan", no_wrap=True)
    table.add_column("Description")
    table.add_row("operator", "Start the agent (default)")
    table.add_row("operator -v / --verbose", "Start the agent and show logs in console")
    table.add_row("operator gateway", "Start the agent with gateway channels")
    table.add_row("operator agent", "Chat with first agent")
    table.add_row(
        "operator agent <name>",
        "Chat with a specific agent  [dim](e.g., operator agent jarvis)[/dim]",
    )
    table.add_row("operator agent <name> -v", "Chat with agent in verbose mode")
    table.add_row(
        "operator agent <name> -s <id>", "Resume a session by ID  [dim](--session / -s)[/dim]"
    )
    table.add_row("operator onboard", "Interactive setup wizard")
    table.add_row("operator agents list", "List all configured agents")
    table.add_row(
        "operator agents add <id>",
        "Add a new agent  [dim](--provider / --model / --workspace)[/dim]",
    )
    table.add_row(
        "operator agents remove <id>",
        "Remove an agent  [dim](--delete-workspace to also wipe files)[/dim]",
    )
    table.add_row("operator status", "Show config and agent status")
    table.add_row("operator sessions", "List conversation sessions")
    table.add_row("operator logs", "Show agent logs  [dim](--follow / -f to tail)[/dim]")
    table.add_row("operator channels", "Show configured channel accounts")
    table.add_row("operator channel add", "Add a new channel interactively")
    table.add_row("operator auth antigravity", "Login with Google Cloud Code Assist OAuth")
    table.add_row("operator auth claude-code", "Login with Claude Code OAuth")
    table.add_row("operator auth codex", "Login with OpenAI Codex (ChatGPT) OAuth")
    table.add_row("operator auth github-copilot", "Login with GitHub Copilot OAuth")
    table.add_row("operator heartbeat enable", "Enable the heartbeat")
    table.add_row("operator heartbeat disable", "Disable the heartbeat")
    table.add_row("operator models", "Show model providers and active LLM/STT/TTS")
    table.add_row("operator config list", "List all config values")
    table.add_row(
        "operator config get <key>", "Get a config value  [dim](e.g. agent.llm.model)[/dim]"
    )
    table.add_row("operator config set <key> <val>", "Set a config value")
    table.add_row("operator config unset <key>", "Remove a config key")
    table.add_row("operator cron list", "List scheduled cron jobs")
    table.add_row("operator cron add", "Add a cron job")
    table.add_row("operator cron remove <id>", "Remove a cron job")
    table.add_row("operator cron enable <id>", "Enable a cron job")
    table.add_row("operator cron disable <id>", "Disable a cron job")
    table.add_row("operator acp sessions", "List local ACP sessions")
    table.add_row("operator acp discover", "Scan local network for Operator ACP servers")
    table.add_row("operator acp list <url>", "Discover agents on a remote ACP server")
    table.add_row("operator acp run <url> <msg>", "Send a message to a remote ACP agent")
    table.add_row("operator acp stdio", "Start ACP stdio bridge (for IDE integration)")
    console.print(table)
    console.print(
        "[dim]Use [bold]operator <command> --help[/bold] for details on any command.[/dim]\n"
    )


@app.command("onboard")
def onboard():
    """Launch the interactive configuration wizard."""
    try:
        from operator_use.cli import setup

        setup.run_initial_setup()
    except ImportError as e:
        print(f"Error: Unable to start configuration UI. Missing dependencies? ({e})")


@app.command("gateway")
def gateway(verbose: bool = typer.Option(False, "--verbose", "-v", help="Show logs in console")):
    """Start the agent with gateway channels (auto-onboards on first run if no config exists)."""
    if not (USERDATA_DIR / "config.json").exists():
        from operator_use.cli import setup

        setup.run_first_install()
    from operator_use.cli.start import run

    run(verbose=verbose)


@app.command("status")
def status():
    """Show agent configuration and status."""
    config_path = USERDATA_DIR / "config.json"
    if not config_path.exists():
        console.print("[red]No config found.[/red] Run [bold]operator onboard[/bold] first.")
        raise typer.Exit(1)

    config = json.loads(config_path.read_text(encoding="utf-8"))
    agent = config.get("agent", {})
    llm = agent.get("llmConfig", agent.get("llm_config", agent.get("llm", {})))
    channels = config.get("channels", {})
    crons_path = USERDATA_DIR / "crons.json"
    workspaces_dir = USERDATA_DIR / "workspaces"
    sessions_dir = (
        next(
            (d / "sessions" for d in sorted(workspaces_dir.iterdir()) if (d / "sessions").exists()),
            None,
        )
        if workspaces_dir.exists()
        else None
    )
    log_file = USERDATA_DIR / "operator.log"

    # LLM
    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 1))
    table.add_column("Key", style="bold cyan")
    table.add_column("Value")
    table.add_row("Provider", llm.get("provider") or "-")
    table.add_row("Model", llm.get("model") or "-")

    # Channels
    active = [
        name
        for name, cfg in channels.items()
        if isinstance(cfg, dict)
        and (cfg.get("token") or cfg.get("botToken") or cfg.get("bot_token"))
    ]
    table.add_row("Channels", ", ".join(active) if active else "[dim]none configured[/dim]")

    # Sessions
    session_count = (
        len(list(sessions_dir.glob("*.jsonl"))) if sessions_dir and sessions_dir.exists() else 0
    )
    table.add_row("Sessions", str(session_count))

    # Crons
    cron_count = 0
    if crons_path.exists():
        try:
            crons = json.loads(crons_path.read_text(encoding="utf-8"))
            cron_count = len(crons) if isinstance(crons, list) else 0
        except json.JSONDecodeError:
            pass
    table.add_row("Cron jobs", str(cron_count))

    # Log file
    table.add_row("Log file", str(log_file) if log_file.exists() else "[dim]not yet created[/dim]")

    console.print("\n[bold]Operator Status[/bold]")
    console.print(table)


@app.command("sessions")
def sessions(limit: int = typer.Option(20, "--limit", "-n", help="Max sessions to show.")):
    """List stored conversation sessions."""
    workspaces_dir = USERDATA_DIR / "workspaces"
    files = []
    if workspaces_dir.exists():
        for agent_dir in workspaces_dir.iterdir():
            sd = agent_dir / "sessions"
            if sd.exists():
                files.extend(sd.glob("*.jsonl"))
    if not files:
        console.print("[dim]No sessions found.[/dim]")
        return

    files = sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        console.print("[dim]No sessions found.[/dim]")
        return

    table = Table(box=box.ROUNDED, show_header=True, padding=(0, 1))
    table.add_column("Session ID", style="cyan")
    table.add_column("Messages", justify="right")
    table.add_column("Created")
    table.add_column("Updated")

    for f in files[:limit]:
        created, updated, msg_count = "-", "-", 0
        try:
            with open(f, encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    if obj.get("type") == "metadata":
                        created = obj.get("created_at", "-")[:19].replace("T", " ")
                        updated = obj.get("updated_at", "-")[:19].replace("T", " ")
                    elif "role" in obj:
                        msg_count += 1
        except (json.JSONDecodeError, OSError):
            pass
        table.add_row(f.stem, str(msg_count), created, updated)

    console.print(f"\n[bold]Sessions[/bold] ({min(len(files), limit)} of {len(files)})")
    console.print(table)


channel_app = typer.Typer(
    name="channel",
    help="Manage connected channels.",
    invoke_without_command=True,
    no_args_is_help=True,
)
app.add_typer(channel_app)


@channel_app.command("add")
def channel_add():
    """Add a new channel to your existing config."""
    config_path = USERDATA_DIR / "config.json"
    if not config_path.exists():
        console.print("[red]No config found.[/red] Run [bold]operator onboard[/bold] first.")
        raise typer.Exit(1)

    import json as _json
    from operator_use.cli.setup import configure_channel
    from operator_use.cli.tui import NavigateBack
    from operator_use.config import ChannelsConfig

    data = _json.loads(config_path.read_text(encoding="utf-8"))
    existing = ChannelsConfig(**data.get("channels", {}))
    try:
        updated = configure_channel(existing)
    except NavigateBack:
        console.print("[yellow]Channel setup cancelled.[/yellow]")
        raise typer.Exit(1)

    data.setdefault("channels", {})
    data["channels"].update(updated.model_dump(by_alias=True, exclude_none=True))
    config_path.write_text(_json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    console.print("[green]Channel added.[/green] Run [bold]operator run[/bold] to start.")


cron_app = typer.Typer(
    name="cron",
    help="Manage scheduled cron jobs.",
    invoke_without_command=True,
    no_args_is_help=True,
)
app.add_typer(cron_app)


def _load_cron():
    from operator_use.crons.service import Cron

    return Cron(store_path=USERDATA_DIR / "crons.json")


def _ms_to_str(ms: int | None) -> str:
    if ms is None:
        return "[dim]-[/dim]"
    from datetime import datetime

    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


@cron_app.command("list")
def cron_list():
    """List all cron jobs."""
    cron = _load_cron()
    jobs = cron.list_jobs()
    if not jobs:
        console.print("[dim]No cron jobs found.[/dim]")
        return
    table = Table(box=box.ROUNDED, show_header=True, padding=(0, 1))
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Schedule")
    table.add_column("Status")
    table.add_column("Last Run")
    table.add_column("Next Run")
    for j in jobs:
        sched = j.schedule.expr or f"every {j.schedule.interval_ms}ms"
        enabled = "[green]enabled[/green]" if j.enabled else "[dim]disabled[/dim]"
        last_status = j.state.last_status or "-"
        if last_status == "failure":
            last_status = f"[red]{last_status}[/red]"
        elif last_status == "success":
            last_status = f"[green]{last_status}[/green]"
        row_status = f"{enabled}  {last_status}"
        table.add_row(
            j.id[:8],
            j.name,
            sched,
            row_status,
            _ms_to_str(j.state.last_run_at_ms),
            _ms_to_str(j.state.next_run_at_ms),
        )
    console.print(f"\n[bold]Cron Jobs[/bold] ({len(jobs)})")
    console.print(table)


@cron_app.command("add")
def cron_add(
    name: str = typer.Argument(..., help="Job name"),
    expr: str = typer.Argument(..., help='Cron expression, e.g. "*/5 * * * *"'),
    message: str = typer.Argument(..., help="Message to send when job fires"),
    channel: str = typer.Option(
        ..., "--channel", "-c", help="Channel name (telegram, discord, slack)"
    ),
    chat_id: str = typer.Option(..., "--chat-id", "-i", help="Chat/user ID to send to"),
    deliver: bool = typer.Option(False, "--deliver", help="Deliver directly (skip agent)"),
    tz: str = typer.Option("UTC", "--tz", help="Timezone, e.g. Asia/Kolkata"),
):
    """Add a new cron job."""
    from operator_use.crons.views import CronSchedule, CronPayload

    cron = _load_cron()
    schedule = CronSchedule(mode="cron", expr=expr, tz=tz)
    payload = CronPayload(message=message, deliver=deliver, channel=channel, chat_id=chat_id)
    job = cron.add_job(name=name, schedule=schedule, payload=payload)
    console.print(
        f"[green]Added[/green] job [bold]{job.name}[/bold] ({job.id[:8]})  next run: {_ms_to_str(job.state.next_run_at_ms)}"
    )


@cron_app.command("remove")
def cron_remove(job_id: str = typer.Argument(..., help="Job ID (or prefix)")):
    """Remove a cron job."""
    cron = _load_cron()
    jobs = [j for j in cron.list_jobs() if j.id.startswith(job_id)]
    if not jobs:
        console.print(f"[yellow]No job found matching:[/yellow] {job_id}")
        raise typer.Exit(1)
    if len(jobs) > 1:
        console.print(f"[yellow]Ambiguous ID — {len(jobs)} matches. Use more characters.[/yellow]")
        raise typer.Exit(1)
    cron.remove_job(jobs[0].id)
    console.print(f"[green]Removed[/green] job [bold]{jobs[0].name}[/bold]")


@cron_app.command("enable")
def cron_enable(job_id: str = typer.Argument(..., help="Job ID (or prefix)")):
    """Enable a cron job."""
    cron = _load_cron()
    jobs = [j for j in cron.list_jobs() if j.id.startswith(job_id)]
    if not jobs:
        console.print(f"[yellow]No job found matching:[/yellow] {job_id}")
        raise typer.Exit(1)
    cron.update_job(jobs[0].id, enabled=True)
    console.print(f"[green]Enabled[/green] job [bold]{jobs[0].name}[/bold]")


@cron_app.command("disable")
def cron_disable(job_id: str = typer.Argument(..., help="Job ID (or prefix)")):
    """Disable a cron job."""
    cron = _load_cron()
    jobs = [j for j in cron.list_jobs() if j.id.startswith(job_id)]
    if not jobs:
        console.print(f"[yellow]No job found matching:[/yellow] {job_id}")
        raise typer.Exit(1)
    cron.update_job(jobs[0].id, enabled=False)
    console.print(f"[dim]Disabled[/dim] job [bold]{jobs[0].name}[/bold]")


config_app = typer.Typer(
    name="config",
    help="Get, set, or unset config values.",
    invoke_without_command=True,
    no_args_is_help=True,
)
app.add_typer(config_app)


def _load_config_json() -> tuple[dict, Path]:
    config_path = USERDATA_DIR / "config.json"
    if not config_path.exists():
        console.print("[red]No config found.[/red] Run [bold]operator onboard[/bold] first.")
        raise typer.Exit(1)
    return json.loads(config_path.read_text(encoding="utf-8")), config_path


def _save_config_json(data: dict, path: Path) -> None:
    path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")


def _get_nested(data: dict, keys: list[str]):
    for k in keys:
        if not isinstance(data, dict) or k not in data:
            return None
        data = data[k]
    return data


def _set_nested(data: dict, keys: list[str], value) -> None:
    for k in keys[:-1]:
        data = data.setdefault(k, {})
    data[keys[-1]] = value


def _unset_nested(data: dict, keys: list[str]) -> bool:
    for k in keys[:-1]:
        if not isinstance(data, dict) or k not in data:
            return False
        data = data[k]
    if keys[-1] in data:
        del data[keys[-1]]
        return True
    return False


def _coerce(value: str):
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _flatten(data: dict, prefix: str = "") -> list[tuple[str, str]]:
    rows = []
    for k, v in data.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            rows.extend(_flatten(v, full_key))
        else:
            rows.append((full_key, str(v)))
    return rows


@config_app.command("list")
def config_list():
    """List all config values."""
    data, _ = _load_config_json()
    table = Table(box=box.ROUNDED, show_header=True, padding=(0, 1))
    table.add_column("Key", style="cyan")
    table.add_column("Value")
    for key, val in _flatten(data):
        # mask secrets
        if any(s in key for s in ("token", "api_key", "apiKey", "secret", "password")):
            val = f"{val[:16]}...{val[-8:]}" if len(val) > 24 else val
        table.add_row(key, val)
    console.print(table)


@config_app.command("get")
def config_get(key: str = typer.Argument(..., help="Dot-separated key, e.g. agent.llm.provider")):
    """Get a config value."""
    data, _ = _load_config_json()
    value = _get_nested(data, key.split("."))
    if value is None:
        console.print(f"[yellow]Key not found:[/yellow] {key}")
        raise typer.Exit(1)
    console.print(value)


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Dot-separated key, e.g. agent.llm.model"),
    value: str = typer.Argument(..., help="Value to set"),
):
    """Set a config value."""
    data, path = _load_config_json()
    _set_nested(data, key.split("."), _coerce(value))
    _save_config_json(data, path)
    console.print(f"[green]Set[/green] {key} = {value}")


@config_app.command("unset")
def config_unset(key: str = typer.Argument(..., help="Dot-separated key to remove")):
    """Remove a config key."""
    data, path = _load_config_json()
    if _unset_nested(data, key.split(".")):
        _save_config_json(data, path)
        console.print(f"[green]Unset[/green] {key}")
    else:
        console.print(f"[yellow]Key not found:[/yellow] {key}")
        raise typer.Exit(1)


@app.command("channels")
def channels():
    """Show configured channel accounts and their status."""
    config_path = USERDATA_DIR / "config.json"
    if not config_path.exists():
        console.print("[red]No config found.[/red] Run [bold]operator onboard[/bold] first.")
        raise typer.Exit(1)

    config = json.loads(config_path.read_text(encoding="utf-8"))
    ch = config.get("channels", {})

    CHANNEL_DEFS = [
        ("Telegram", ch.get("telegram", {}), ["token"]),
        ("Discord", ch.get("discord", {}), ["token"]),
        ("Slack", ch.get("slack", {}), ["botToken", "bot_token"]),
    ]

    table = Table(box=box.ROUNDED, show_header=True, padding=(0, 1))
    table.add_column("Channel", style="bold")
    table.add_column("Status")
    table.add_column("Token")
    table.add_column("Allow From")

    for name, cfg, token_keys in CHANNEL_DEFS:
        token = next((cfg.get(k, "") for k in token_keys if cfg.get(k)), "")
        configured = bool(token)
        status = "[green]active[/green]" if configured else "[dim]not configured[/dim]"
        masked = (
            f"{token[:16]}...{token[-8:]}"
            if len(token) > 24
            else ("[dim]-[/dim]" if not token else token)
        )
        allow = ", ".join(cfg.get("allowFrom", cfg.get("allow_from", []))) or "[dim]all[/dim]"
        table.add_row(name, status, masked, allow)

    console.print("\n[bold]Channels[/bold]")
    console.print(table)


@app.command("models")
def models():
    """Show configured model providers and active LLM/STT/TTS settings."""
    config_path = USERDATA_DIR / "config.json"
    if not config_path.exists():
        console.print("[red]No config found.[/red] Run [bold]operator onboard[/bold] first.")
        raise typer.Exit(1)

    config = json.loads(config_path.read_text(encoding="utf-8"))
    agent = config.get("agent", {})
    llm = agent.get("llmConfig", agent.get("llm_config", agent.get("llm", {})))
    stt = agent.get("sttConfig", agent.get("stt_config", agent.get("stt", {})))
    tts = agent.get("ttsConfig", agent.get("tts_config", agent.get("tts", {})))
    providers = config.get("providers", {})

    # Active models panel
    active_table = Table(box=box.ROUNDED, show_header=False, padding=(0, 1))
    active_table.add_column("Key", style="bold cyan")
    active_table.add_column("Value")
    active_table.add_row("LLM", f"{llm.get('provider', '-')}  /  {llm.get('model', '-')}")
    if stt.get("enabled"):
        active_table.add_row("STT", f"{stt.get('provider', '-')}  /  {stt.get('model', '-')}")
    if tts.get("enabled"):
        active_table.add_row(
            "TTS",
            f"{tts.get('provider', '-')}  /  {tts.get('model', '-')}  (voice: {tts.get('voice', '-')})",
        )

    console.print("\n[bold]Active Models[/bold]")
    console.print(active_table)

    # Providers table
    prov_table = Table(box=box.ROUNDED, show_header=True, padding=(0, 1))
    prov_table.add_column("Provider", style="bold")
    prov_table.add_column("API Key")
    prov_table.add_column("Base URL")

    for name, cfg in providers.items():
        if not isinstance(cfg, dict):
            continue
        key = cfg.get("apiKey", cfg.get("api_key", ""))
        base = cfg.get("apiBase", cfg.get("api_base", "")) or "[dim]-[/dim]"
        masked_key = (
            f"{key[:16]}...{key[-8:]}"
            if len(key) > 24
            else ("[dim]not set[/dim]" if not key else key)
        )
        active_marker = " [green]*[/green]" if name == llm.get("provider") else ""
        prov_table.add_row(name.replace("_", " ").title() + active_marker, masked_key, base)

    console.print("\n[bold]Providers[/bold]  [dim]([green]*[/green] = active LLM)[/dim]")
    console.print(prov_table)


acp_app = typer.Typer(
    name="acp",
    help="Manage ACP (Agent Communication Protocol) sessions and connections.",
    invoke_without_command=True,
    no_args_is_help=True,
)
app.add_typer(acp_app)


def _load_acp_sessions() -> dict:
    path = USERDATA_DIR / "acp_sessions.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


@acp_app.command("sessions")
def acp_sessions():
    """List active ACP sessions tracked locally."""
    sessions = _load_acp_sessions()
    if not sessions:
        console.print("[dim]No active ACP sessions.[/dim]")
        return
    table = Table(box=box.ROUNDED, show_header=True, padding=(0, 1))
    table.add_column("Session ID", style="cyan")
    table.add_column("Label")
    table.add_column("Agent")
    table.add_column("Server")
    table.add_column("Last Updated")
    for sid, info in sessions.items():
        table.add_row(
            sid,
            info.get("label") or "[dim]-[/dim]",
            info.get("agent_id") or "[dim]-[/dim]",
            info.get("base_url") or "[dim]-[/dim]",
            (info.get("last_updated") or "-")[:19].replace("T", " "),
        )
    console.print(f"\n[bold]ACP Sessions[/bold] ({len(sessions)})")
    console.print(table)


@acp_app.command("list")
def acp_list(
    url: str = typer.Argument(
        ..., help="Base URL of the remote ACP server, e.g. http://localhost:9000"
    ),
    token: str = typer.Option(
        "", "--token", "-t", help="Bearer token if the server requires auth."
    ),
):
    """Discover agents available on a remote ACP server."""
    from operator_use.acp.client import ACPClient
    from operator_use.acp.config import ACPClientConfig

    async def _run():
        cfg = ACPClientConfig(enabled=True, base_url=url, agent_id="operator", auth_token=token)
        async with ACPClient(cfg) as client:
            resp = await client.list_agents()
        return resp

    try:
        resp = asyncio.run(_run())
    except Exception as e:
        console.print(f"[red]Failed to connect to {url}:[/red] {e}")
        raise typer.Exit(1)

    if not resp.agents:
        console.print(f"[dim]No agents found at {url}[/dim]")
        return

    table = Table(box=box.ROUNDED, show_header=True, padding=(0, 1))
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Description")
    table.add_column("Capabilities")
    for a in resp.agents:
        caps = a.capabilities
        cap_str = (
            ", ".join(
                c
                for c, on in [
                    ("streaming", caps.streaming),
                    ("async", caps.async_mode),
                    ("sessions", caps.session),
                ]
                if on
            )
            or "[dim]-[/dim]"
        )
        table.add_row(a.id, a.name, a.description or "[dim]-[/dim]", cap_str)

    console.print(f"\n[bold]Agents at {url}[/bold]")
    console.print(table)


@acp_app.command("stdio")
def acp_stdio():
    """Start ACP stdio bridge for IDE integration (JSON-RPC 2.0 over stdin/stdout).

    Reads agent/run and agent/stream requests from stdin, writes JSON-RPC 2.0
    responses to stdout.  All logging and startup output goes to stderr so
    stdout stays clean for the IDE.

    Configure in VS Code / Zed / etc. as the agent server command:
        operator acp stdio
    """
    import sys
    from rich.console import Console as _Console

    os.environ["OPERATOR_STDIO"] = "1"
    # Redirect the runner's startup banner to stderr so stdout stays clean JSON-RPC
    from operator_use.cli import start as _start

    _start._console = _Console(file=sys.stderr)
    asyncio.run(_start.main())


@acp_app.command("run")
def acp_run(
    url: str = typer.Argument(..., help="Base URL of the remote ACP server."),
    message: str = typer.Argument(..., help="Message to send."),
    agent: str = typer.Option(
        "", "--agent", "-a", help="Target agent ID (auto-detected if omitted)."
    ),
    session: str = typer.Option(
        "", "--session", "-s", help="Session ID for multi-turn conversations."
    ),
    token: str = typer.Option(
        "", "--token", "-t", help="Bearer token if the server requires auth."
    ),
):
    """Send a one-shot message to a remote ACP agent and print the response."""
    from operator_use.acp.client import ACPClient
    from operator_use.acp.config import ACPClientConfig

    async def _run():
        cfg = ACPClientConfig(
            enabled=True, base_url=url, agent_id=agent or "operator", auth_token=token
        )
        async with ACPClient(cfg) as client:
            if not agent:
                try:
                    agents_resp = await client.list_agents()
                    if agents_resp.agents:
                        cfg.agent_id = agents_resp.agents[0].id
                except Exception:
                    pass
            return await client.run(message, session_id=session or None)

    try:
        result = asyncio.run(_run())
    except Exception as e:
        console.print(f"[red]ACP run failed:[/red] {e}")
        raise typer.Exit(1)

    console.print(result)


@acp_app.command("discover")
def acp_discover(
    port: int = typer.Option(8765, "--port", "-p", help="ACP server port to scan for."),
    timeout: float = typer.Option(0.5, "--timeout", "-t", help="Seconds to wait per host."),
    add: bool = typer.Option(
        False, "--add", help="Interactively add discovered servers to acp_agents config."
    ),
):
    """Scan the local network for running Operator ACP servers.

    Discovers all machines on your WiFi/LAN that are running an Operator ACP
    server on the given port and shows their agent info.

    Example:
        operator acp discover
        operator acp discover --port 8765 --add
    """
    import ipaddress
    import socket
    import aiohttp

    async def _probe(session: aiohttp.ClientSession, ip: str) -> dict | None:
        url = f"http://{ip}:{port}/agents"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {"ip": ip, "url": f"http://{ip}:{port}", "data": data}
        except Exception:
            pass
        return None

    async def _scan() -> list[dict]:
        # Determine local subnet
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
        hosts = [str(h) for h in network.hosts() if str(h) != local_ip]

        connector = aiohttp.TCPConnector(limit=100)
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [_probe(session, ip) for ip in hosts]
            results = await asyncio.gather(*tasks)

        return [r for r in results if r is not None]

    console.print(f"\n[dim]Scanning local network for ACP servers on port {port}...[/dim]")
    try:
        found = asyncio.run(_scan())
    except Exception as e:
        console.print(f"[red]Scan failed:[/red] {e}")
        raise typer.Exit(1)

    if not found:
        console.print(f"[dim]No Operator ACP servers found on port {port}.[/dim]")
        console.print(
            "[dim]Make sure the other machine has[/dim] [bold]acp_server.enabled = true[/bold] [dim]in config.json.[/dim]"
        )
        return

    table = Table(box=box.ROUNDED, show_header=True, padding=(0, 1))
    table.add_column("IP", style="cyan")
    table.add_column("URL")
    table.add_column("Agent ID")
    table.add_column("Name")
    table.add_column("Description")

    for entry in found:
        agents = entry["data"].get("agents", [])
        if agents:
            for a in agents:
                table.add_row(
                    entry["ip"],
                    entry["url"],
                    a.get("id", "-"),
                    a.get("name", "-"),
                    a.get("description", "-") or "[dim]-[/dim]",
                )
        else:
            table.add_row(entry["ip"], entry["url"], "[dim]-[/dim]", "[dim]-[/dim]", "[dim]-[/dim]")

    console.print(f"\n[bold]Found {len(found)} ACP server(s)[/bold]")
    console.print(table)

    if add:
        import json as _json

        config_path = USERDATA_DIR / "config.json"
        if not config_path.exists():
            console.print(
                "[red]No config.json found.[/red] Run [bold]operator onboard[/bold] first."
            )
            raise typer.Exit(1)
        data = _json.loads(config_path.read_text(encoding="utf-8"))
        acp_agents = data.setdefault("acpAgents", {})

        added = 0
        for entry in found:
            agents = entry["data"].get("agents", [])
            agent_id = agents[0].get("id", "operator") if agents else "operator"
            agent_name = agents[0].get("name", entry["ip"]) if agents else entry["ip"]
            # Suggest a name: use hostname if resolvable, else last octet
            try:
                suggested_name = socket.gethostbyaddr(entry["ip"])[0].split(".")[0].lower()
            except Exception:
                suggested_name = f"operator-{entry['ip'].split('.')[-1]}"

            console.print(f"\nFound [cyan]{entry['url']}[/cyan] — [bold]{agent_name}[/bold]")
            name = typer.prompt(
                f"  Registry name (press Enter to use '{suggested_name}', or skip to ignore)",
                default=suggested_name,
            )
            if not name.strip():
                continue

            acp_agents[name] = {
                "baseUrl": entry["url"],
                "agentId": agent_id,
                "description": f"Auto-discovered: {agent_name} at {entry['ip']}",
            }
            added += 1

        if added:
            config_path.write_text(
                _json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8"
            )
            console.print(
                f"\n[green]Added {added} agent(s) to config.json.[/green] Restart operator to apply."
            )
        else:
            console.print("\n[dim]No agents added.[/dim]")


auth_app = typer.Typer(
    name="auth",
    help="Authenticate with OAuth-based providers.",
    invoke_without_command=True,
    no_args_is_help=True,
)
app.add_typer(auth_app)


@auth_app.command("antigravity")
def auth_antigravity():
    """Login with Google Cloud Code Assist (Antigravity) OAuth."""
    from operator_use.providers.antigravity.auth import login, load_auth

    existing = load_auth()
    if existing and existing.get("access_token"):
        import time as _t

        expires_at = existing.get("expires_at", 0)
        if expires_at > _t.time() + 60:
            console.print(
                f"[green]Already authenticated![/green] Project: {existing.get('project_id', '-')}"
            )
            return
    console.print("[bold]Authenticating with Antigravity (Google Cloud Code Assist)...[/bold]")
    try:
        result = login()
        console.print(f"[green]Logged in![/green] Project: {result.get('project_id', '-')}")
    except Exception as e:
        console.print(f"[red]Auth failed:[/red] {e}")
        raise typer.Exit(1)


@auth_app.command("claude-code")
def auth_claude_code():
    """Login with Claude Code OAuth (token auto-discovered from Claude CLI)."""
    import subprocess
    from operator_use.providers.claude_code.llm import load_claude_code_token

    token = load_claude_code_token()
    if token:
        console.print(f"[green]Already authenticated![/green] Token: {token[:20]}...")
        return
    console.print("[bold]Authenticating with Claude Code...[/bold]")
    console.print("Launching [bold]claude[/bold] login flow...")
    try:
        subprocess.run(["claude", "login"], check=True)
    except FileNotFoundError:
        console.print("[red]claude CLI not found.[/red] Install it with:")
        console.print("  npm install -g @anthropic-ai/claude-code")
        raise typer.Exit(1)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Auth failed:[/red] {e}")
        raise typer.Exit(1)
    token = load_claude_code_token()
    if token:
        console.print(f"[green]Logged in![/green] Token: {token[:20]}...")
    else:
        console.print(
            "[yellow]Token will be auto-discovered from ~/.claude/.credentials.json on next run.[/yellow]"
        )


@auth_app.command("github-copilot")
def auth_github_copilot():
    """Login with GitHub Copilot OAuth (GitHub Device Flow)."""
    from operator_use.providers.github_copilot.auth import load_auth, get_copilot_token

    auth = load_auth()
    if auth and auth.get("github_token"):
        import time as _t

        if auth.get("copilot_expires_at", 0) > _t.time() + 60:
            console.print("[green]Already authenticated![/green] GitHub Copilot token is valid.")
            return
        # Try refreshing the copilot token with existing github token
        try:
            get_copilot_token(auth)
            console.print("[green]Already authenticated![/green] Copilot token refreshed.")
            return
        except Exception:
            pass  # Fall through to re-login

    console.print("[bold]Authenticating with GitHub Copilot...[/bold]")
    try:
        from operator_use.providers.github_copilot.auth import login

        login()
        console.print("[green]Logged in![/green] GitHub Copilot credentials saved.")
    except Exception as e:
        console.print(f"[red]Auth failed:[/red] {e}")
        raise typer.Exit(1)


@auth_app.command("codex")
def auth_codex():
    """Login with OpenAI Codex (ChatGPT subscription) OAuth."""
    import subprocess
    from operator_use.providers.codex.llm import _load_auth

    auth = _load_auth()
    if auth and auth.get("access"):
        console.print(
            f"[green]Already authenticated![/green] Account: {auth.get('account_id', '-')}"
        )
        return
    console.print("[bold]Authenticating with Codex (ChatGPT subscription)...[/bold]")
    console.print("Launching [bold]codex login[/bold]...")
    try:
        subprocess.run(["codex", "login"], check=True)
    except FileNotFoundError:
        console.print("[red]codex CLI not found.[/red] Install it with:")
        console.print("  npm install -g @openai/codex")
        raise typer.Exit(1)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Auth failed:[/red] {e}")
        raise typer.Exit(1)
    auth = _load_auth()
    if auth and auth.get("access"):
        console.print(f"[green]Logged in![/green] Account: {auth.get('account_id', '-')}")
    else:
        console.print(
            "[yellow]Token will be auto-discovered from ~/.codex/auth.json on next run.[/yellow]"
        )


@app.command("agent")
def agent_repl(
    agent_name: str = typer.Argument(
        "", help="Agent name to chat with (optional, uses first agent if not specified)."
    ),
    session: str = typer.Option(
        "", "--session", "-s", help="Session ID to resume (default: new session per run)."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show tool calls and logs in console"
    ),
):
    """Chat directly with the agent in the terminal (no gateway required).

    Usage:
        operator agent                  # Chat with first agent
        operator agent jarvis           # Chat with 'jarvis' agent
        operator agent claude -v        # Chat with 'claude' agent (verbose mode)
    """
    agent_id = agent_name  # Use the positional argument as agent_id
    from operator_use.cli.start import (
        _build_agents,
        _make_stt,
        _make_tts,
        _make_search,
        _make_image,
        copy_templates_to_workspace,
        _resolve_agent_workspace,
        setup_logging,
    )
    from operator_use.config import load_config
    from operator_use.bus import Bus
    from operator_use.orchestrator import Orchestrator
    from operator_use.crons import Cron

    config_path = USERDATA_DIR / "config.json"
    if not config_path.exists():
        console.print("[red]No config found.[/red] Run [bold]operator onboard[/bold] first.")
        raise typer.Exit(1)

    setup_logging(USERDATA_DIR, verbose=verbose)
    config = load_config(USERDATA_DIR)
    if not config.agents.list:
        console.print(
            "[red]No agents defined in config.[/red] Run [bold]operator onboard[/bold] first."
        )
        raise typer.Exit(1)

    # Resolve target agent definition
    if agent_id:
        defn = next((d for d in config.agents.list if d.id == agent_id), None)
        if defn is None:
            ids = ", ".join(d.id for d in config.agents.list)
            console.print(f"[red]Agent '{agent_id}' not found.[/red] Available: {ids}")
            raise typer.Exit(1)
    else:
        defn = config.agents.list[0]

    workspace = _resolve_agent_workspace(defn)
    copy_templates_to_workspace(USERDATA_DIR, workspace=workspace)

    bus = Bus()
    cron_store = USERDATA_DIR / "crons.json"
    cron = Cron(store_path=cron_store)
    image = _make_image(config)
    search = _make_search(config)
    stt = _make_stt(config)
    tts = _make_tts(config)

    agents = _build_agents(config, cron=cron, gateway=None, bus=bus, image=image, search=search)
    agent = agents.get(defn.id)
    if agent is None:
        console.print(f"[red]Failed to initialize agent '{defn.id}'.[/red]")
        raise typer.Exit(1)

    # Restrict to CLI-appropriate tools (no channel, messaging, cron, or multi-agent tools)
    from operator_use.agent.tools.builtin import CLI_TOOLS
    from operator_use.agent.tools.registry import ToolRegistry

    agent.tool_register = ToolRegistry()
    agent.tool_register.register_tools(CLI_TOOLS)
    # Re-inject runtime extensions
    agent.tool_register.set_extension("_agent", agent)
    agent.tool_register.set_extension("_agent_id", defn.id)
    agent.tool_register.set_extension("_bus", bus)
    agent.tool_register.set_extension("_agent_registry", agents)

    # Add tool call hook for verbose mode
    if verbose:
        from operator_use.agent.hooks.events import HookEvent

        async def _log_tool_call(ctx):
            tool_call = ctx.tool_call
            params_str = ", ".join(f"{k}={repr(v)[:50]}" for k, v in tool_call.params.items())
            console.print(f"[dim]→ Tool: {tool_call.name}({params_str})[/dim]")

        agent.hooks.register(HookEvent.BEFORE_TOOL_CALL, _log_tool_call)

    orchestrator = Orchestrator(
        bus=bus,
        agents=agents,
        stt=stt,
        tts=tts,
        default_agent=defn.id,
    )

    import uuid as _uuid

    chat_id = session or f"cli_{_uuid.uuid4().hex[:8]}"
    llm_conf = defn.llm_config
    llm_label = f"{llm_conf.provider} / {llm_conf.model}" if llm_conf else "not configured"
    agent_label = f"[dim]{defn.id}[/dim]  " if len(agents) > 1 or agent_id else ""
    console.print(
        f"\n[bold]Operator Agent[/bold]  {agent_label}[dim]{llm_label}[/dim]  [dim](session: {chat_id})[/dim]"
    )
    console.print("[dim]Type your message and press Enter. Ctrl+C or Ctrl+D to exit.[/dim]\n")

    async def _run():
        nonlocal chat_id
        while True:
            try:
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("You: ")
                )
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Goodbye.[/dim]")
                break
            user_input = user_input.strip()
            if not user_input:
                continue

            state = {"printed": 0, "started": False}

            async def stream_chunk(content: str, is_done: bool, init: bool = False) -> None:
                if init and not state["started"]:
                    console.print(f"\n[bold cyan]{defn.id.title()}:[/bold cyan] ", end="")
                    state["started"] = True
                delta = content[state["printed"] :]
                if delta:
                    console.print(delta, end="", highlight=False)
                    state["printed"] = len(content)
                if is_done:
                    console.print("\n")

            try:
                # Parse slash commands
                if user_input.startswith("/"):
                    parts = user_input.split(None, 1)
                    command = parts[0][1:].lower()
                    args = parts[1] if len(parts) > 1 else ""

                    if command == "start":
                        session_name = args.strip() or None
                        target_session_id = f"{chat_id}:{session_name}" if session_name else chat_id
                        existing = agent.sessions.load(target_session_id)
                        name_label = f" '{session_name}'" if session_name else ""
                        if existing and existing.messages:
                            console.print(
                                f"\nSession{name_label} is already active. Use /stop to end it first.\n"
                            )
                        else:
                            console.print(f"\nSession{name_label} started.\n")
                            from operator_use.orchestrator.commands import _HELP_TEXT

                            console.print(_HELP_TEXT, highlight=False, markup=False)
                            chat_id = target_session_id
                    elif command == "stop":
                        agent.sessions.archive(chat_id)
                        console.print(
                            "\nSession saved and closed.\nUse /start to begin a new session.\n"
                        )
                        chat_id = f"cli_{_uuid.uuid4().hex[:8]}"
                    elif command == "restart":
                        console.print("\nRestarting system. I'll be back in a moment.\n")
                        import os

                        os._exit(75)
                    elif command == "help":
                        from operator_use.orchestrator.commands import _HELP_TEXT

                        console.print(f"\n{_HELP_TEXT}\n", highlight=False, markup=False)
                    else:
                        console.print(f"\n[yellow]Unknown command:[/yellow] /{command}\n")
                else:
                    await orchestrator.process_direct(
                        content=user_input,
                        channel="cli",
                        chat_id=chat_id,
                        publish_stream=stream_chunk,
                    )
                    if not state["started"]:
                        console.print()
            except Exception as e:
                console.print(f"\n[red]Error:[/red] {e}")

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# agents sub-app
# ---------------------------------------------------------------------------

agents_app = typer.Typer(name="agents", help="Manage agents.", no_args_is_help=True)
app.add_typer(agents_app, name="agents")


def _load_and_save_config(mutate):
    """Load config.json, call mutate(data: dict), then write it back."""
    config_path = USERDATA_DIR / "config.json"
    if not config_path.exists():
        console.print("[red]No config found.[/red] Run [bold]operator onboard[/bold] first.")
        raise typer.Exit(1)
    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)
    mutate(data)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


@agents_app.command("list")
def agents_list():
    """List all configured agents."""
    config_path = USERDATA_DIR / "config.json"
    if not config_path.exists():
        console.print("[red]No config found.[/red] Run [bold]operator onboard[/bold] first.")
        raise typer.Exit(1)

    from operator_use.config import load_config
    from operator_use.cli.start import _resolve_agent_workspace

    config = load_config(USERDATA_DIR)
    agent_defs = config.agents.list or []

    if not agent_defs:
        console.print(
            "[dim]No agents configured — a single [bold]default[/bold] agent is used.[/dim]"
        )
        return

    table = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
    table.add_column("ID", style="bold")
    table.add_column("Workspace")
    table.add_column("LLM")

    for defn in agent_defs:
        workspace = str(_resolve_agent_workspace(defn))
        llm = defn.llm_config
        llm_label = f"{llm.provider} / {llm.model}" if llm else "not configured"
        table.add_row(defn.id, workspace, llm_label)

    console.print(table)


@agents_app.command("add")
def agents_add(
    agent_id: str = typer.Argument(..., help="Agent ID (e.g. 'work', 'personal')."),
    workspace: str = typer.Option(
        "",
        "--workspace",
        "-w",
        help="Custom workspace path (default: ~/.operator-use/workspaces/<id>).",
    ),
    provider: str = typer.Option("", "--provider", "-p", help="LLM provider (e.g. 'anthropic')."),
    model: str = typer.Option("", "--model", "-m", help="LLM model (e.g. 'claude-opus-4-6')."),
    prompt_mode: str = typer.Option(
        "", "--prompt-mode", help="Prompt mode: full, minimal, or none."
    ),
    system_prompt: str = typer.Option(
        "", "--system-prompt", "-s", help="Freeform instructions appended to every system prompt."
    ),
    tools_profile: str = typer.Option(
        "", "--tools-profile", help="Tool profile: minimal, coding, or full."
    ),
    tools_allow: list[str] = typer.Option(
        [], "--tools-allow", help="Extra tool names to add on top of the profile."
    ),
    tools_deny: list[str] = typer.Option(
        [], "--tools-deny", help="Tool names to remove from the profile."
    ),
):
    """Add a new agent and create its workspace."""
    from operator_use.cli.start import copy_templates_to_workspace, _resolve_agent_workspace
    from operator_use.config import AgentDefinition, LLMConfig

    defn = AgentDefinition(
        id=agent_id,
        workspace=workspace or None,
        llm_config=LLMConfig(provider=provider, model=model) if provider and model else None,
    )
    ws_path = _resolve_agent_workspace(defn)
    copy_templates_to_workspace(USERDATA_DIR, workspace=ws_path)

    def mutate(data: dict):
        agents = data.setdefault("agents", {})
        lst = agents.setdefault("list", [])
        if any(a.get("id") == agent_id for a in lst):
            console.print(f"[yellow]Agent '{agent_id}' already exists.[/yellow]")
            raise typer.Exit(0)
        entry: dict = {"id": agent_id}
        if workspace:
            entry["workspace"] = workspace
        if provider and model:
            entry["llmConfig"] = {"provider": provider, "model": model}
        if prompt_mode:
            entry["promptMode"] = prompt_mode
        if system_prompt:
            entry["systemPrompt"] = system_prompt
        if tools_profile or tools_allow or tools_deny:
            tools_entry: dict = {}
            if tools_profile:
                tools_entry["profile"] = tools_profile
            if tools_allow:
                tools_entry["alsoAllow"] = tools_allow
            if tools_deny:
                tools_entry["deny"] = tools_deny
            entry["tools"] = tools_entry
        lst.append(entry)

    _load_and_save_config(mutate)
    console.print(f"[green]Agent '{agent_id}' added.[/green] Workspace: {ws_path}")


@agents_app.command("remove")
def agents_remove(
    agent_id: str = typer.Argument(..., help="Agent ID to remove."),
    delete_workspace: bool = typer.Option(
        False, "--delete-workspace", "-d", help="Also delete the workspace directory."
    ),
):
    """Remove an agent from config and optionally delete its workspace."""
    from operator_use.config import load_config
    from operator_use.cli.start import _resolve_agent_workspace

    config = load_config(USERDATA_DIR)
    defn = next((a for a in config.agents.list if a.id == agent_id), None)
    ws_path = _resolve_agent_workspace(defn) if defn else None

    def mutate(data: dict):
        lst = data.get("agents", {}).get("list", [])
        before = len(lst)
        data["agents"]["list"] = [a for a in lst if a.get("id") != agent_id]
        if len(data["agents"]["list"]) == before:
            console.print(f"[yellow]Agent '{agent_id}' not found.[/yellow]")
            raise typer.Exit(1)
        data["bindings"] = [b for b in data.get("bindings", []) if b.get("agentId") != agent_id]

    _load_and_save_config(mutate)

    if delete_workspace and ws_path and ws_path.exists():
        import shutil

        shutil.rmtree(ws_path)
        console.print(f"[green]Agent '{agent_id}' removed.[/green] Workspace deleted: {ws_path}")
    else:
        console.print(f"[green]Agent '{agent_id}' removed.[/green] Workspace kept: {ws_path}")


# ---------------------------------------------------------------------------
# heartbeat sub-app
# ---------------------------------------------------------------------------

heartbeat_app = typer.Typer(
    name="heartbeat",
    help="Enable or disable the heartbeat.",
    invoke_without_command=True,
    no_args_is_help=True,
)
app.add_typer(heartbeat_app, name="heartbeat")


def _set_heartbeat(enabled: bool) -> None:
    import json as _json

    config_path = USERDATA_DIR / "config.json"
    if not config_path.exists():
        console.print("[red]No config found.[/red] Run [bold]operator onboard[/bold] first.")
        raise typer.Exit(1)
    data = _json.loads(config_path.read_text(encoding="utf-8"))
    data.setdefault("heartbeat", {})["enabled"] = enabled
    config_path.write_text(_json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    state = "[green]enabled[/green]" if enabled else "[dim]disabled[/dim]"
    console.print(f"Heartbeat {state}. Restart [bold]operator[/bold] to apply.")


@heartbeat_app.command("enable")
def heartbeat_enable():
    """Enable the heartbeat (periodic self-maintenance)."""
    _set_heartbeat(True)


@heartbeat_app.command("disable")
def heartbeat_disable():
    """Disable the heartbeat."""
    _set_heartbeat(False)


# ---------------------------------------------------------------------------


@app.command("logs")
def logs(
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show."),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output in real time."),
):
    """Show agent logs."""
    log_file = USERDATA_DIR / "operator.log"
    if not log_file.exists():
        console.print(
            "[dim]No log file found. Start the agent first with [bold]operator run[/bold].[/dim]"
        )
        raise typer.Exit(1)

    # Show last N lines
    with open(log_file, encoding="utf-8") as f:
        all_lines = f.readlines()
    for line in all_lines[-lines:]:
        console.print(line.rstrip())

    if follow:
        console.print("[dim]--- following (Ctrl+C to stop) ---[/dim]")
        with open(log_file, encoding="utf-8") as f:
            f.seek(0, 2)  # seek to end
            try:
                while True:
                    line = f.readline()
                    if line:
                        console.print(line.rstrip())
                    else:
                        time.sleep(0.3)
            except KeyboardInterrupt:
                pass

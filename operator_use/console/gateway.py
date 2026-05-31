from __future__ import annotations

import asyncio
import os
import platform
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import click

from operator_use.settings.paths import (
    get_gateway_dir,
    get_gateway_pid_path,
    get_gateway_stderr_path,
    get_gateway_stdout_path,
)

SERVICE_ID = "com.operator.gateway"
SYSTEMD_UNIT = "operator-gateway.service"
WINDOWS_TASK = r"Operator\Gateway"


@dataclass(frozen=True)
class GatewayOptions:
    cwd: Path
    model: str | None = None
    provider: str | None = None
    resume: bool = False
    system_prompt: str | None = None
    prompt: str | None = None          # inject as first user message on startup
    session_file: Path | None = None   # open a specific session file (internal, used by reboot)


async def run_gateway_foreground(options: GatewayOptions) -> None:
    from operator_use.gateway.manager import GatewayManager
    from operator_use.runtime import Runtime, RuntimeConfig

    config = RuntimeConfig(
        cwd=options.cwd,
        model_id=options.model or "claude-sonnet-4-6",
        provider=options.provider,
        resume=options.resume,
        system_prompt=options.system_prompt,
        session_file=options.session_file,
    )
    runtime = await Runtime.create(config)
    gateway_manager = GatewayManager(runtime)
    gateway_manager.start()
    await asyncio.sleep(0.5)

    # Register gateway shutdown so runtime.ashutdown() stops channels first.
    # This ensures Telegram polling stops before the new process starts,
    # preventing the Conflict: terminated by other getUpdates error.
    runtime._gateway_shutdown = gateway_manager.astop

    channel_ids = list(gateway_manager.gateway._channels.keys())
    channels_str = ", ".join(channel_ids) if channel_ids else "none"
    click.echo(f"Agent running in {options.cwd}  (model: {config.model_id})")
    click.echo(f"Channels: {channels_str}")
    click.echo("Press Ctrl-C to stop.\n")

    # Signal parent process (if launched via Popen reboot) that startup succeeded
    _signal_ready()

    # Resume prompt: from --prompt, or from the environment when a reboot routed
    # it back to its originating channel (OPERATOR_PROMPT*).
    prompt = options.prompt or os.environ.get("OPERATOR_PROMPT")
    if prompt:
        await _inject_prompt(
            prompt, runtime,
            channel=os.environ.get("OPERATOR_PROMPT_CHANNEL"),
            chat_id=os.environ.get("OPERATOR_PROMPT_CHAT"),
        )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_handlers: list[signal.Signals] = []

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
            installed_handlers.append(sig)
        except (NotImplementedError, RuntimeError, ValueError):
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))

    try:
        await stop.wait()
    finally:
        for sig in installed_handlers:
            try:
                loop.remove_signal_handler(sig)
            except (RuntimeError, ValueError):
                pass
        click.echo("\nShutting down...")
        await runtime.ashutdown()


def _signal_ready() -> None:
    """Write to OPERATOR_READY_FD if present — tells the parent Popen reboot we started OK."""
    fd_str = os.environ.get("OPERATOR_READY_FD", "")
    if not fd_str:
        return
    try:
        fd = int(fd_str)
        os.write(fd, b"ready")
        os.close(fd)
    except OSError:
        pass


async def _inject_prompt(prompt: str, runtime, channel: str | None = None, chat_id: str | None = None) -> None:
    """Publish an initial prompt so the agent starts immediately.

    Delivered to the channel/chat the prompt was routed to (e.g. the channel a
    reboot was requested from); falls back to the stdio terminal.
    """
    from operator_use.bus.types import IncomingMessage, TextPart

    await runtime.bus.publish_incoming(IncomingMessage(
        channel=channel or "stdio",
        chat_id=chat_id or "cli",
        parts=[TextPart(content=prompt)],
        metadata={"source": "initial_prompt"},
    ))


def foreground_command(options: GatewayOptions) -> list[str]:
    command = [*_operator_command(), "gateway", "--cwd", str(options.cwd)]
    if options.model:
        command.extend(["--model", options.model])
    if options.provider:
        command.extend(["--provider", options.provider])
    if options.resume:
        command.append("--resume")
    if options.system_prompt:
        command.extend(["--system-prompt", options.system_prompt])
    command.append("run")
    return command


def service_installed() -> bool:
    system = platform.system()
    if system == "Darwin":
        return _launchd_plist_path().exists()
    if system == "Linux":
        return _systemd_unit_path().exists()
    if system == "Windows":
        return _windows_task_exists()
    return False


def install_service(options: GatewayOptions) -> None:
    get_gateway_dir().mkdir(parents=True, exist_ok=True)
    system = platform.system()
    if system == "Darwin":
        _install_launchd(options)
    elif system == "Linux":
        _install_systemd(options)
    elif system == "Windows":
        _install_windows_task(options)
    else:
        raise click.ClickException(f"Gateway install is not supported on {system or 'this platform'}.")


def uninstall_service() -> None:
    system = platform.system()
    if system == "Darwin":
        _uninstall_launchd()
    elif system == "Linux":
        _uninstall_systemd()
    elif system == "Windows":
        _uninstall_windows_task()
    else:
        raise click.ClickException(f"Gateway uninstall is not supported on {system or 'this platform'}.")


def start_gateway(options: GatewayOptions) -> None:
    if service_installed():
        _start_service()
        return
    _start_pid_process(options)


def stop_gateway() -> None:
    if service_installed():
        _stop_service()
        return
    _stop_pid_process()


def gateway_status() -> str:
    installed = service_installed()
    if installed:
        service_status = "running" if _service_running() else "not running"
        return f"Gateway: {service_status} (installed service)"

    pid = _read_pid()
    pid_status = f"pid {pid} running" if pid and _pid_running(pid) else "not running"
    return f"Gateway: {pid_status} (manual background process)"


def _operator_command() -> list[str]:
    override = os.environ.get("OPERATOR_EXECUTABLE")
    if override:
        return [override]

    argv0 = Path(sys.argv[0])
    if argv0.name and argv0.name != "-":
        if argv0.is_absolute() and argv0.exists():
            return [str(argv0)]
        if argv0.exists():
            return [str(argv0.resolve())]
        found = shutil.which(argv0.name)
        if found:
            return [found]

    found = shutil.which("operator")
    if found:
        return [found]

    return [sys.executable, "-m", "operator_use.console.main"]


def _start_pid_process(options: GatewayOptions) -> None:
    pid = _read_pid()
    if pid and _pid_running(pid):
        click.echo(f"Gateway already running (pid {pid}).")
        return

    get_gateway_dir().mkdir(parents=True, exist_ok=True)
    stdout = get_gateway_stdout_path().open("ab")
    stderr = get_gateway_stderr_path().open("ab")
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    proc = subprocess.Popen(
        foreground_command(options),
        cwd=str(options.cwd),
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        start_new_session=os.name != "nt",
        creationflags=creationflags,
    )
    _write_pid(proc.pid)
    click.echo(f"Gateway started in background (pid {proc.pid}).")


def _stop_pid_process() -> None:
    pid = _read_pid()
    if not pid:
        click.echo("Gateway is not running.")
        return
    if not _pid_running(pid):
        get_gateway_pid_path().unlink(missing_ok=True)
        click.echo("Gateway is not running.")
        return

    _terminate_pid(pid)
    get_gateway_pid_path().unlink(missing_ok=True)
    click.echo("Gateway stopped.")


def _read_pid() -> int | None:
    try:
        value = get_gateway_pid_path().read_text(encoding="utf-8").strip()
        return int(value) if value else None
    except (FileNotFoundError, ValueError):
        return None


def _write_pid(pid: int) -> None:
    get_gateway_dir().mkdir(parents=True, exist_ok=True)
    get_gateway_pid_path().write_text(f"{pid}\n", encoding="utf-8")


def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _terminate_pid(pid: int, timeout: float = 10.0) -> None:
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_running(pid):
            return
        time.sleep(0.1)
    sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
    os.kill(pid, sigkill)


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{SERVICE_ID}.plist"


def _install_launchd(options: GatewayOptions) -> None:
    plist = _launchd_plist(options)
    path = _launchd_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plist, encoding="utf-8")
    _run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], check=False)
    click.echo(f"Installed launchd service: {path}")


def _uninstall_launchd() -> None:
    path = _launchd_plist_path()
    _run(["launchctl", "bootout", f"gui/{os.getuid()}/{SERVICE_ID}"], check=False)
    path.unlink(missing_ok=True)
    click.echo("Uninstalled launchd service.")


def _start_service() -> None:
    system = platform.system()
    if system == "Darwin":
        _run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(_launchd_plist_path())], check=False)
        _run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{SERVICE_ID}"])
        click.echo("Gateway service started.")
    elif system == "Linux":
        _run(["systemctl", "--user", "start", SYSTEMD_UNIT])
        click.echo("Gateway service started.")
    elif system == "Windows":
        _run(["schtasks", "/Run", "/TN", WINDOWS_TASK])
        click.echo("Gateway task started.")


def _stop_service() -> None:
    system = platform.system()
    if system == "Darwin":
        _run(["launchctl", "bootout", f"gui/{os.getuid()}/{SERVICE_ID}"], check=False)
        click.echo("Gateway service stopped.")
    elif system == "Linux":
        _run(["systemctl", "--user", "stop", SYSTEMD_UNIT])
        click.echo("Gateway service stopped.")
    elif system == "Windows":
        _run(["schtasks", "/End", "/TN", WINDOWS_TASK], check=False)
        click.echo("Gateway task stopped.")


def _service_running() -> bool:
    system = platform.system()
    if system == "Darwin":
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{SERVICE_ID}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0
    if system == "Linux":
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", SYSTEMD_UNIT],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0
    if system == "Windows":
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", WINDOWS_TASK, "/FO", "LIST", "/V"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0 and "Running" in result.stdout
    return False


def _launchd_plist(options: GatewayOptions) -> str:
    args = "\n".join(f"        <string>{_xml_escape(arg)}</string>" for arg in foreground_command(options))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{SERVICE_ID}</string>
    <key>ProgramArguments</key>
    <array>
{args}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>{_xml_escape(str(options.cwd))}</string>
    <key>StandardOutPath</key>
    <string>{_xml_escape(str(get_gateway_stdout_path()))}</string>
    <key>StandardErrorPath</key>
    <string>{_xml_escape(str(get_gateway_stderr_path()))}</string>
</dict>
</plist>
"""


def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / SYSTEMD_UNIT


def _install_systemd(options: GatewayOptions) -> None:
    path = _systemd_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_systemd_unit(options), encoding="utf-8")
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", SYSTEMD_UNIT])
    click.echo(f"Installed systemd user service: {path}")


def _uninstall_systemd() -> None:
    _run(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT], check=False)
    path = _systemd_unit_path()
    path.unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"], check=False)
    click.echo("Uninstalled systemd user service.")


def _systemd_unit(options: GatewayOptions) -> str:
    command = " ".join(shlex.quote(arg) for arg in foreground_command(options))
    return f"""[Unit]
Description=Operator Gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={options.cwd}
ExecStart={command}
Restart=always
RestartSec=5
KillSignal=SIGTERM
StandardOutput=append:{get_gateway_stdout_path()}
StandardError=append:{get_gateway_stderr_path()}

[Install]
WantedBy=default.target
"""


def _windows_task_exists() -> bool:
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", WINDOWS_TASK],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _install_windows_task(options: GatewayOptions) -> None:
    command = _windows_task_command(foreground_command(options), options.cwd)
    _run(
        [
            "schtasks",
            "/Create",
            "/F",
            "/TN",
            WINDOWS_TASK,
            "/SC",
            "ONLOGON",
            "/TR",
            command,
        ]
    )
    click.echo(f"Installed Windows scheduled task: {WINDOWS_TASK}")


def _uninstall_windows_task() -> None:
    _run(["schtasks", "/Delete", "/F", "/TN", WINDOWS_TASK], check=False)
    click.echo("Uninstalled Windows scheduled task.")


def _windows_task_command(command: Sequence[str], cwd: Path) -> str:
    quoted = subprocess.list2cmdline(list(command))
    return f'cmd /c "cd /d {subprocess.list2cmdline([str(cwd)])} && {quoted}"'


def _run(command: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=check)


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _options(cwd: str | None, model: str | None, provider: str | None, resume: bool, system_prompt: str | None, prompt: str | None = None, session_file: str | None = None) -> GatewayOptions:
    return GatewayOptions(
        cwd=Path(cwd).resolve() if cwd else Path.cwd(),
        model=model,
        provider=provider,
        resume=resume,
        system_prompt=system_prompt,
        prompt=prompt,
        session_file=Path(session_file) if session_file else None,
    )


def _options_from_context(
    ctx: click.Context,
    cwd: str | None,
    model: str | None,
    provider: str | None,
    resume: bool,
    system_prompt: str | None,
    prompt: str | None = None,
    session_file: str | None = None,
) -> GatewayOptions:
    base = ctx.obj.get("gateway_options") if ctx.obj else None
    return GatewayOptions(
        cwd=Path(cwd).resolve() if cwd else (base.cwd if base else Path.cwd()),
        model=model if model is not None else (base.model if base else None),
        provider=provider if provider is not None else (base.provider if base else None),
        resume=resume or (base.resume if base else False),
        system_prompt=system_prompt if system_prompt is not None else (base.system_prompt if base else None),
        prompt=prompt or (base.prompt if base else None),
        session_file=Path(session_file) if session_file else (base.session_file if base else None),
    )


def gateway_options(command):
    command = click.option("--session-file", default=None, hidden=True, help="Open a specific session file.")(command)
    command = click.option("--prompt", default=None, help="Inject an initial message so the agent starts immediately.")(command)
    command = click.option("--system-prompt", default=None, help="Override the default system prompt")(command)
    command = click.option("--resume", is_flag=True, default=False, help="Resume the most recent session instead of starting fresh")(command)
    command = click.option("--provider", default=None, help="Provider override")(command)
    command = click.option("--model", default=None, help="Model ID (runtime override)")(command)
    command = click.option("--cwd", default=None, type=click.Path(exists=True, file_okay=False), help="Working directory")(command)
    return command


@click.group("gateway", invoke_without_command=True)
@click.pass_context
@click.option("--cwd", default=None, type=click.Path(exists=True, file_okay=False), help="Working directory")
@click.option("--model", default=None, help="Model ID (runtime override)")
@click.option("--provider", default=None, help="Provider override")
@click.option("--resume", is_flag=True, default=False, help="Resume the most recent session instead of starting fresh")
@click.option("--system-prompt", default=None, help="Override the default system prompt")
@click.option("--prompt", default=None, help="Inject an initial message so the agent starts immediately.")
@click.option("--session-file", default=None, hidden=True, help="Open a specific session file.")
def gateway(ctx: click.Context, cwd: str | None, model: str | None, provider: str | None, resume: bool, system_prompt: str | None, prompt: str | None, session_file: str | None) -> None:
    """Run and manage the Operator gateway."""
    ctx.ensure_object(dict)
    ctx.obj["gateway_options"] = _options(cwd, model, provider, resume, system_prompt, prompt, session_file)
    if ctx.invoked_subcommand is None:
        asyncio.run(run_gateway_foreground(ctx.obj["gateway_options"]))


@gateway.command("run")
@click.pass_context
@gateway_options
def gateway_run(ctx: click.Context, cwd: str | None, model: str | None, provider: str | None, resume: bool, system_prompt: str | None, prompt: str | None, session_file: str | None) -> None:
    """Run the gateway in the foreground."""
    asyncio.run(run_gateway_foreground(_options_from_context(ctx, cwd, model, provider, resume, system_prompt, prompt, session_file)))


@gateway.command("start")
@click.pass_context
@gateway_options
def gateway_start(ctx: click.Context, cwd: str | None, model: str | None, provider: str | None, resume: bool, system_prompt: str | None, prompt: str | None, session_file: str | None) -> None:
    """Start the installed service or a background gateway process."""
    start_gateway(_options_from_context(ctx, cwd, model, provider, resume, system_prompt, prompt, session_file))


@gateway.command("stop")
def gateway_stop() -> None:
    """Stop the installed service or background gateway process."""
    stop_gateway()


@gateway.command("status")
def gateway_status_command() -> None:
    """Show gateway service status."""
    click.echo(gateway_status())


@gateway.command("install")
@click.pass_context
@gateway_options
def gateway_install(ctx: click.Context, cwd: str | None, model: str | None, provider: str | None, resume: bool, system_prompt: str | None, prompt: str | None, session_file: str | None) -> None:
    """Install the gateway to start at login."""
    install_service(_options_from_context(ctx, cwd, model, provider, resume, system_prompt, prompt, session_file))


@gateway.command("uninstall")
def gateway_uninstall() -> None:
    """Remove the gateway login service."""
    uninstall_service()

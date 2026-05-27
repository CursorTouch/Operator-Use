from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from program.console import gateway as gateway_cli
from program.console.gateway import GatewayOptions
from program.console.main import cli


def test_foreground_command_uses_gateway_run_with_group_options(monkeypatch, tmp_path):
    monkeypatch.setenv("OPERATOR_EXECUTABLE", "/bin/operator")
    options = GatewayOptions(
        cwd=tmp_path,
        model="m1",
        provider="p1",
        resume=True,
        system_prompt="system",
    )

    assert gateway_cli.foreground_command(options) == [
        "/bin/operator",
        "gateway",
        "--cwd",
        str(tmp_path),
        "--model",
        "m1",
        "--provider",
        "p1",
        "--resume",
        "--system-prompt",
        "system",
        "run",
    ]


def test_launchd_plist_runs_foreground_gateway(monkeypatch, tmp_path):
    monkeypatch.setenv("OPERATOR_EXECUTABLE", "/bin/operator")
    options = GatewayOptions(cwd=tmp_path, model="claude")

    plist = gateway_cli._launchd_plist(options)

    assert "<string>com.operator.gateway</string>" in plist
    assert "<string>/bin/operator</string>" in plist
    assert "<string>gateway</string>" in plist
    assert "<string>run</string>" in plist
    assert "<key>KeepAlive</key>" in plist
    assert str(tmp_path) in plist


def test_systemd_unit_runs_foreground_gateway(monkeypatch, tmp_path):
    monkeypatch.setenv("OPERATOR_EXECUTABLE", "/bin/operator")
    options = GatewayOptions(cwd=tmp_path, provider="anthropic")

    unit = gateway_cli._systemd_unit(options)

    assert "Description=Operator Gateway" in unit
    assert "ExecStart=/bin/operator gateway --cwd" in unit
    assert "--provider anthropic run" in unit
    assert "Restart=always" in unit
    assert "WantedBy=default.target" in unit


def test_windows_task_command_wraps_foreground_gateway(monkeypatch, tmp_path):
    monkeypatch.setenv("OPERATOR_EXECUTABLE", r"C:\Operator\operator.exe")
    command = gateway_cli.foreground_command(GatewayOptions(cwd=tmp_path))

    task_command = gateway_cli._windows_task_command(command, tmp_path)

    assert task_command.startswith('cmd /c "cd /d ')
    assert "operator.exe" in task_command
    assert "gateway" in task_command
    assert "run" in task_command


def test_gateway_status_reports_manual_pid(monkeypatch, tmp_path):
    monkeypatch.setattr(gateway_cli, "service_installed", lambda: False)
    monkeypatch.setattr(gateway_cli, "_read_pid", lambda: 123)
    monkeypatch.setattr(gateway_cli, "_pid_running", lambda pid: pid == 123)

    assert gateway_cli.gateway_status() == "Gateway: pid 123 running (manual background process)"


def test_gateway_status_reports_installed_service(monkeypatch):
    monkeypatch.setattr(gateway_cli, "service_installed", lambda: True)
    monkeypatch.setattr(gateway_cli, "_service_running", lambda: True)

    assert gateway_cli.gateway_status() == "Gateway: running (installed service)"


def test_gateway_status_command_is_registered():
    runner = CliRunner()
    result = runner.invoke(cli, ["gateway", "--help"])

    assert result.exit_code == 0
    assert "install" in result.output
    assert "start" in result.output
    assert "uninstall" in result.output


def test_gateway_start_accepts_command_options(monkeypatch, tmp_path):
    runner = CliRunner()
    seen: list[GatewayOptions] = []
    monkeypatch.setattr(gateway_cli, "start_gateway", lambda options: seen.append(options))

    result = runner.invoke(
        cli,
        ["gateway", "start", "--cwd", str(tmp_path), "--model", "m1", "--provider", "p1", "--resume"],
    )

    assert result.exit_code == 0
    assert seen == [GatewayOptions(cwd=tmp_path, model="m1", provider="p1", resume=True, prompt=None, session_file=None)]


def test_install_systemd_writes_unit_and_enables(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    unit_path = tmp_path / "operator-gateway.service"
    monkeypatch.setenv("OPERATOR_EXECUTABLE", "/bin/operator")
    monkeypatch.setattr(gateway_cli, "_systemd_unit_path", lambda: unit_path)
    monkeypatch.setattr(gateway_cli.platform, "system", lambda: "Linux")
    monkeypatch.setattr(gateway_cli, "_run", lambda command, check=True: calls.append(list(command)) or subprocess.CompletedProcess(command, 0))
    monkeypatch.setattr(gateway_cli, "get_gateway_dir", lambda: tmp_path / "gateway")

    gateway_cli.install_service(GatewayOptions(cwd=tmp_path))

    assert unit_path.exists()
    assert ["systemctl", "--user", "daemon-reload"] in calls
    assert ["systemctl", "--user", "enable", "operator-gateway.service"] in calls

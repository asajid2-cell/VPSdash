from __future__ import annotations

from typing import Any

from .execution import is_windows_remote_mode, run_host_local_command, run_remote_command


def _local_checks_for_mode(host_mode: str) -> list[dict[str, Any]]:
    if host_mode == "windows-local":
        return [
            {"title": "Windows host", "command": "hostname", "timeout": 15, "use_wsl": False},
            {"title": "WSL distros", "command": "wsl.exe -l -v", "timeout": 20, "use_wsl": False},
            {"title": "WSL user", "command": "whoami", "timeout": 10},
            {"title": "WSL kernel", "command": "uname -a", "timeout": 15},
            {"title": "WSL libvirt", "command": "virsh uri || true", "timeout": 15},
            {"title": "WSL Docker version", "command": "docker --version", "timeout": 15},
            {"title": "WSL Docker Compose version", "command": "docker compose version", "timeout": 15},
        ]
    if host_mode == "linux-local":
        return [
            {"title": "User", "command": "whoami", "timeout": 10},
            {"title": "Kernel", "command": "uname -a", "timeout": 10},
            {"title": "Docker version", "command": "docker --version", "timeout": 15},
            {"title": "Docker Compose version", "command": "docker compose version", "timeout": 15},
            {"title": "Nginx version", "command": "nginx -v", "timeout": 15},
            {"title": "Memory", "command": "free -h", "timeout": 15},
            {"title": "Disk", "command": "df -h /", "timeout": 15},
        ]
    return []


def run_diagnostics(host: dict[str, Any], project: dict[str, Any] | None = None) -> dict[str, Any]:
    host_mode = host.get("mode", "remote-linux")
    checks: list[dict[str, Any]] = []

    if host_mode in {"remote-linux", "windows-remote", "windows-wsl-remote"}:
        if host.get("bootstrap_auth") == "password-bootstrap":
            message = (
                "Password bootstrap is still selected. Use the connection packet in Setup for the first manual SSH login "
                "from Computer A, install or copy your SSH key, then switch this profile to SSH key already ready "
                "before running automated diagnostics."
            )
            return {
                "summary": {"total": 1, "ok": 0, "failed": 1},
                "checks": [{"title": "Remote bootstrap gate", "ok": False, "stderr": message, "stdout": "", "command": ""}],
            }
        remote_checks: list[dict[str, Any]] = []
        if is_windows_remote_mode(host):
            remote_checks.extend(
                [
                    {"title": "Remote Windows host", "command": "hostname", "timeout": 15, "use_wsl": False},
                    {"title": "WSL distros", "command": "wsl.exe -l -v", "timeout": 20, "use_wsl": False},
                ]
            )
        remote_checks.extend(
            [
            {"title": "Remote user", "command": "whoami", "timeout": 15},
            {"title": "Hostname", "command": "hostname", "timeout": 15},
            {"title": "OS release", "command": ". /etc/os-release && echo \"$NAME $VERSION\"", "timeout": 15},
            {"title": "Docker version", "command": "docker --version", "timeout": 20},
            {"title": "Docker Compose version", "command": "docker compose version", "timeout": 20},
            {"title": "Memory", "command": "free -h", "timeout": 15},
            {"title": "Disk", "command": "df -h /", "timeout": 15},
            {"title": "Swap", "command": "swapon --show || true", "timeout": 15},
            ]
        )
        if project and project.get("deploy_path"):
            deploy_path = project["deploy_path"]
            remote_checks.extend(
                [
                    {"title": "Repo path", "command": f"cd {deploy_path} && pwd", "timeout": 15},
                    {"title": "Git branch", "command": f"cd {deploy_path} && git branch --show-current", "timeout": 15},
                    {"title": "Compose status", "command": f"cd {deploy_path} && docker compose ps", "timeout": 30},
                ]
            )
        for check in remote_checks:
            result = run_remote_command(host, check["command"], timeout=check["timeout"], use_wsl=check.get("use_wsl"))
            checks.append({"title": check["title"], **result})
    else:
        for check in _local_checks_for_mode(host_mode):
            result = run_host_local_command(host, check["command"], timeout=check["timeout"], use_wsl=check.get("use_wsl"))
            checks.append({"title": check["title"], **result})

    ok_count = sum(1 for item in checks if item.get("ok"))
    return {"summary": {"total": len(checks), "ok": ok_count, "failed": len(checks) - ok_count}, "checks": checks}


def run_monitor_snapshot(host: dict[str, Any], project: dict[str, Any] | None = None) -> dict[str, Any]:
    host_mode = host.get("mode", "remote-linux")
    if host_mode in {"remote-linux", "windows-remote", "windows-wsl-remote"}:
        if host.get("bootstrap_auth") == "password-bootstrap":
            return {
                "bootstrap": {
                    "ok": False,
                    "stdout": "",
                    "stderr": (
                        "Password bootstrap is still selected. Capture the first SSH login manually from Computer A, "
                        "then switch the profile to SSH key already ready before requesting an automated snapshot."
                    ),
                    "command": "",
                }
            }
        snapshot_commands: dict[str, dict[str, Any]] = {}
        if is_windows_remote_mode(host):
            snapshot_commands["wsl"] = {"command": "wsl.exe -l -v", "use_wsl": False}
        snapshot_commands.update({
            "uptime": "uptime",
            "memory": "free -h",
            "disk": "df -h /",
            "listeners": "ss -tln",
        })
        if project and project.get("deploy_path"):
            snapshot_commands["containers"] = {"command": f"cd {project['deploy_path']} && docker compose ps"}
        result = {}
        for key, command_spec in snapshot_commands.items():
            if isinstance(command_spec, str):
                command_spec = {"command": command_spec}
            result[key] = run_remote_command(
                host,
                command_spec["command"],
                timeout=20,
                use_wsl=command_spec.get("use_wsl"),
            )
        return result

    if host_mode == "windows-local":
        snapshot_commands = {
            "system": {"command": "hostname", "use_wsl": False},
            "wsl": {"command": "wsl.exe -l -v", "use_wsl": False},
            "docker": {"command": "docker ps"},
            "libvirt": {"command": "virsh list --all || true"},
        }
    else:
        snapshot_commands = {
            "uptime": {"command": "uptime"},
            "memory": {"command": "free -h"},
            "disk": {"command": "df -h /"},
            "docker": {"command": "docker ps"},
        }
    result = {}
    for key, command_spec in snapshot_commands.items():
        result[key] = run_host_local_command(
            host,
            command_spec["command"],
            timeout=20,
            use_wsl=command_spec.get("use_wsl"),
        )
    return result

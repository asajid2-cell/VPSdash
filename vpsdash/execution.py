from __future__ import annotations

import base64
import platform
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any


class PlanCancelled(Exception):
    pass


WINDOWS_LOCAL_MODES = {"windows-local", "windows-wsl-local"}
WINDOWS_REMOTE_MODES = {"windows-remote", "windows-wsl-remote"}
LINUX_LOCAL_MODES = {"linux-local", "linux-hypervisor"}


def _subprocess_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if platform.system().lower().startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
    return kwargs


def run_local_command(command: str, timeout: int = 60) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        **_subprocess_kwargs(),
    )
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "command": command,
    }


def run_local_command_bytes(command: str, timeout: int = 60) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        timeout=timeout,
        **_subprocess_kwargs(),
    )
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.decode("utf-8", errors="replace"),
        "stderr": completed.stderr.decode("utf-8", errors="replace"),
        "stdout_bytes": completed.stdout,
        "stderr_bytes": completed.stderr,
        "command": command,
    }


def _host_mode(host: dict[str, Any]) -> str:
    return str(host.get("mode") or host.get("host_mode") or "").strip().lower()


def is_windows_local_mode(host: dict[str, Any]) -> bool:
    return _host_mode(host) in WINDOWS_LOCAL_MODES


def is_windows_remote_mode(host: dict[str, Any]) -> bool:
    return _host_mode(host) in WINDOWS_REMOTE_MODES


def is_remote_mode(host: dict[str, Any]) -> bool:
    host_mode = _host_mode(host)
    if host_mode in WINDOWS_LOCAL_MODES or host_mode in LINUX_LOCAL_MODES:
        return False
    if host_mode in WINDOWS_REMOTE_MODES or host_mode == "remote-linux":
        return True
    return bool(str(host.get("ssh_host") or "").strip())


def _should_use_wsl(host: dict[str, Any], use_wsl: bool | None, *, remote: bool) -> bool:
    if use_wsl is not None:
        return bool(use_wsl)
    return is_windows_remote_mode(host) if remote else is_windows_local_mode(host)


def _bash_path_argument(path: str) -> str:
    normalized = str(path or "").replace("\\", "/")
    if normalized == "~":
        return '"$HOME"'
    if normalized.startswith("~/"):
        return f'"$HOME"/{shlex.quote(normalized[2:])}'
    return shlex.quote(normalized)


def _wrap_root_shell_command(command: str) -> str:
    return f"sudo -n bash -lc {shlex.quote(command)}"


def build_wsl_command(host: dict[str, Any], linux_command: str, *, as_root: bool = False) -> str:
    distro = str(host.get("wsl_distribution") or "Ubuntu").strip() or "Ubuntu"
    command_text = str(linux_command or "")
    if any(marker in command_text for marker in ("\n", "\r")):
        encoded = base64.b64encode(command_text.encode("utf-8")).decode("ascii")
        command_text = f"printf %s {shlex.quote(encoded)} | base64 -d | bash"
    parts = ["wsl.exe"]
    if as_root:
        parts.extend(["-u", "root"])
    parts.extend(["-d", distro, "--", "bash", "-lc", command_text])
    return subprocess.list2cmdline(parts)


def build_ssh_command(host: dict[str, Any], remote_command: str) -> str:
    port = str(host.get("ssh_port") or 22)
    user = host.get("ssh_user") or "root"
    hostname = host.get("ssh_host") or ""
    key_path = (host.get("ssh_key_path") or "").strip()
    strict_mode = host.get("strict_host_key_checking", "accept-new")

    parts = [
        "ssh",
        "-o",
        f"StrictHostKeyChecking={strict_mode}",
        "-o",
        "BatchMode=yes",
        "-p",
        port,
    ]
    if key_path:
        parts.extend(["-i", key_path])
    parts.append(f"{user}@{hostname}")
    parts.append(remote_command)
    if platform.system().lower().startswith("win"):
        return subprocess.list2cmdline(parts)
    return " ".join(shlex.quote(part) for part in parts)


def build_scp_fetch_command(host: dict[str, Any], remote_path: str, local_path: str | Path) -> str:
    port = str(host.get("ssh_port") or 22)
    user = host.get("ssh_user") or "root"
    hostname = host.get("ssh_host") or ""
    key_path = (host.get("ssh_key_path") or "").strip()
    strict_mode = host.get("strict_host_key_checking", "accept-new")
    parts = [
        "scp",
        "-o",
        f"StrictHostKeyChecking={strict_mode}",
        "-o",
        "BatchMode=yes",
        "-P",
        port,
    ]
    if key_path:
        parts.extend(["-i", key_path])
    parts.append(f"{user}@{hostname}:{remote_path}")
    parts.append(str(local_path))
    if platform.system().lower().startswith("win"):
        return subprocess.list2cmdline(parts)
    return " ".join(shlex.quote(part) for part in parts)


def run_remote_command(
    host: dict[str, Any],
    remote_command: str,
    timeout: int = 60,
    *,
    use_wsl: bool | None = None,
    run_as_root: bool = False,
) -> dict[str, Any]:
    if _should_use_wsl(host, use_wsl, remote=True):
        wrapped = build_wsl_command(host, remote_command, as_root=run_as_root)
    elif run_as_root:
        wrapped = _wrap_root_shell_command(remote_command)
    else:
        wrapped = remote_command
    return run_local_command(build_ssh_command(host, wrapped), timeout=timeout)


def fetch_remote_file(host: dict[str, Any], remote_path: str, local_path: str | Path, timeout: int = 600) -> dict[str, Any]:
    if is_windows_remote_mode(host):
        command = f"cat {_bash_path_argument(str(remote_path))}"
        result = run_local_command_bytes(
            build_ssh_command(host, build_wsl_command(host, command)),
            timeout=timeout,
        )
        if result.get("ok"):
            destination = Path(local_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(result.get("stdout_bytes") or b"")
        return result
    return run_local_command(build_scp_fetch_command(host, remote_path, local_path), timeout=timeout)


def run_host_local_command(
    host: dict[str, Any],
    command: str,
    timeout: int = 60,
    *,
    use_wsl: bool | None = None,
    run_as_root: bool = False,
) -> dict[str, Any]:
    if _should_use_wsl(host, use_wsl, remote=False):
        return run_local_command(build_wsl_command(host, command, as_root=run_as_root), timeout=timeout)
    if run_as_root and not platform.system().lower().startswith("win"):
        return run_local_command(_wrap_root_shell_command(command), timeout=timeout)
    return run_local_command(command, timeout=timeout)


def resolve_local_wsl_path(host: dict[str, Any], linux_path: str, timeout: int = 60) -> Path:
    linux_literal = _bash_path_argument(str(linux_path))
    result = run_local_command(build_wsl_command(host, f"wslpath -w {linux_literal}"), timeout=timeout)
    if not result.get("ok"):
        raise RuntimeError(result.get("stderr") or f"Could not translate WSL path: {linux_path}")
    translated = str(result.get("stdout") or "").strip().strip('"')
    if not translated:
        raise RuntimeError(f"WSL path translation returned an empty path for {linux_path}")
    return Path(translated)


def _bash_session_command(inner_command: str, *, banner: str | None = None) -> str:
    fragments: list[str] = []
    if banner:
        safe_banner = str(banner).replace("\\", "\\\\").replace('"', '\\"')
        fragments.append(f'printf "%s\\n\\n" "{safe_banner}"')
    fragments.append(inner_command)
    fragments.append('status=$?')
    fragments.append('printf "\\nSession ended with exit code %s.\\n" "$status"')
    fragments.append('printf "Press Enter to close this window... "')
    fragments.append("read _ || true")
    fragments.append('exit "$status"')
    return "; ".join(fragments)


def _encoded_bash_session(script: str) -> str:
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    return f'eval "$(printf %s {shlex.quote(encoded)} | base64 -d)"'


def _extract_ipv4_addresses(text: str) -> list[str]:
    matches = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b", text or "")
    values: list[str] = []
    for match in matches:
        ip = str(match).split("/", 1)[0]
        if ip.startswith("127.") or ip == "0.0.0.0":
            continue
        if ip not in values:
            values.append(ip)
    return values


def _discover_doplet_ip_addresses(host: dict[str, Any], slug: str) -> list[str]:
    command = (
        f"virsh domifaddr {shlex.quote(slug)} --source agent 2>/dev/null || "
        f"virsh domifaddr {shlex.quote(slug)} --source lease 2>/dev/null || "
        f"virsh domifaddr {shlex.quote(slug)} 2>/dev/null || true"
    )
    if is_remote_mode(host):
        result = run_remote_command(host, command, timeout=30, run_as_root=False)
        if not result.get("ok") or not str(result.get("stdout") or "").strip():
            result = run_remote_command(host, command, timeout=30, run_as_root=True)
    else:
        result = run_host_local_command(host, command, timeout=30, run_as_root=False)
        if not result.get("ok") or not str(result.get("stdout") or "").strip():
            result = run_host_local_command(host, command, timeout=30, run_as_root=True)
    return _extract_ipv4_addresses(str(result.get("stdout") or ""))


def describe_doplet_terminal(host: dict[str, Any], doplet: dict[str, Any]) -> dict[str, Any]:
    host_mode = _host_mode(host)
    slug = str(doplet.get("slug") or "").strip()
    if not slug:
        raise ValueError("Doplet slug is required for terminal access.")
    name = str(doplet.get("name") or slug).strip() or slug
    status = str(doplet.get("status") or "draft").strip().lower()
    distro = str(host.get("wsl_distribution") or "Ubuntu").strip() or "Ubuntu"
    bootstrap_user = str(doplet.get("bootstrap_user") or "ubuntu").strip() or "ubuntu"
    ip_addresses = [str(item).strip() for item in doplet.get("ip_addresses") or [] if str(item).strip()]
    if not ip_addresses and status not in {"draft", "planned", "deleted"}:
        try:
            ip_addresses = _discover_doplet_ip_addresses(host, slug)
        except Exception:
            ip_addresses = []
    primary_ip = ip_addresses[0] if ip_addresses else ""
    if status in {"draft", "planned"}:
        return {
            "supported": False,
            "transport": "",
            "launcher": "",
            "title": f"{name} terminal",
            "target": slug,
            "preview_command": "",
            "bootstrap_user": bootstrap_user,
            "ip_addresses": ip_addresses,
            "wsl_distribution": distro if host_mode in WINDOWS_LOCAL_MODES | WINDOWS_REMOTE_MODES else "",
            "reason": "Create the Doplet first before opening a terminal.",
            "status": status,
        }
    if status in {"provisioning", "queued"} and not primary_ip:
        return {
            "supported": False,
            "transport": "",
            "launcher": "",
            "title": f"{name} terminal",
            "target": slug,
            "preview_command": "",
            "bootstrap_user": bootstrap_user,
            "ip_addresses": ip_addresses,
            "wsl_distribution": distro if host_mode in WINDOWS_LOCAL_MODES | WINDOWS_REMOTE_MODES else "",
            "reason": "Provisioning is still running. Wait for the Doplet to finish before opening a terminal.",
            "status": status,
        }
    if status == "deleted":
        return {
            "supported": False,
            "transport": "",
            "launcher": "",
            "title": f"{name} terminal",
            "target": slug,
            "preview_command": "",
            "bootstrap_user": bootstrap_user,
            "ip_addresses": ip_addresses,
            "wsl_distribution": distro if host_mode in WINDOWS_LOCAL_MODES | WINDOWS_REMOTE_MODES else "",
            "reason": "This Doplet has already been deleted.",
            "status": status,
        }

    if host_mode in WINDOWS_LOCAL_MODES:
        if primary_ip:
            inner_command = f"ssh -o StrictHostKeyChecking=accept-new {bootstrap_user}@{primary_ip}"
            preview_command = build_wsl_command(host, inner_command)
            return {
                "supported": True,
                "transport": "ssh",
                "launcher": "windows-wsl",
                "title": f"{name} terminal",
                "target": primary_ip,
                "preview_command": preview_command,
                "bootstrap_user": bootstrap_user,
                "ip_addresses": ip_addresses,
                "wsl_distribution": distro,
                "reason": "",
                "inner_command": inner_command,
                "access_label": f"SSH {bootstrap_user}@{primary_ip}",
                "status": status,
            }
        inner_command = f"virsh console {slug}"
        preview_command = build_wsl_command(host, inner_command, as_root=True)
        return {
            "supported": True,
            "transport": "virsh-console",
            "launcher": "windows-wsl",
            "title": f"{name} console",
            "target": slug,
            "preview_command": preview_command,
            "bootstrap_user": bootstrap_user,
            "ip_addresses": ip_addresses,
            "wsl_distribution": distro,
            "reason": "",
            "inner_command": inner_command,
            "requires_root": True,
            "access_label": f"Console {slug} via local WSL",
            "status": status,
        }

    if host_mode in LINUX_LOCAL_MODES:
        if primary_ip:
            inner_command = f"ssh -o StrictHostKeyChecking=accept-new {bootstrap_user}@{primary_ip}"
            return {
                "supported": True,
                "transport": "ssh",
                "launcher": "linux-local",
                "title": f"{name} terminal",
                "target": primary_ip,
                "preview_command": inner_command,
                "bootstrap_user": bootstrap_user,
                "ip_addresses": ip_addresses,
                "wsl_distribution": "",
                "reason": "",
                "inner_command": inner_command,
                "access_label": f"SSH {bootstrap_user}@{primary_ip}",
                "status": status,
            }
        inner_command = f"sudo virsh console {slug}"
        return {
            "supported": True,
            "transport": "virsh-console",
            "launcher": "linux-local",
            "title": f"{name} console",
            "target": slug,
            "preview_command": inner_command,
            "bootstrap_user": bootstrap_user,
            "ip_addresses": ip_addresses,
            "wsl_distribution": "",
            "reason": "",
            "inner_command": inner_command,
            "requires_root": True,
            "access_label": f"Console {slug} on local hypervisor",
            "status": status,
        }

    return {
        "supported": False,
        "transport": "",
        "launcher": "",
        "title": f"{name} terminal",
        "target": primary_ip or slug,
        "preview_command": "",
        "bootstrap_user": bootstrap_user,
        "ip_addresses": ip_addresses,
        "wsl_distribution": distro if host_mode in WINDOWS_REMOTE_MODES else "",
        "reason": "Direct terminal launch is currently available only for local Linux hosts or Windows hosts running Doplets through local WSL.",
        "status": status,
    }


def _launch_windows_terminal(host: dict[str, Any], inner_command: str, *, title: str, as_root: bool = False) -> None:
    session_command = _bash_session_command(inner_command, banner=title)
    distro = str(host.get("wsl_distribution") or "Ubuntu").strip() or "Ubuntu"
    safe_command = _encoded_bash_session(session_command)
    kwargs: dict[str, Any] = {}
    if platform.system().lower().startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen(
        [
            "wsl.exe",
            *([] if not as_root else ["-u", "root"]),
            "-d",
            distro,
            "--",
            "bash",
            "-lc",
            safe_command,
        ],
        **kwargs,
    )


def _launch_linux_terminal(inner_command: str, *, title: str) -> None:
    session_command = _bash_session_command(inner_command, banner=title)
    terminal_variants = [
        ["x-terminal-emulator", "-T", title, "-e", "bash", "-lc", session_command],
        ["gnome-terminal", "--title", title, "--", "bash", "-lc", session_command],
        ["konsole", "--hold", "-p", f"tabtitle={title}", "-e", "bash", "-lc", session_command],
        ["xterm", "-T", title, "-e", "bash", "-lc", session_command],
    ]
    for args in terminal_variants:
        if shutil.which(args[0]):
            subprocess.Popen(args)
            return
    raise RuntimeError("No supported terminal emulator found. Install x-terminal-emulator, gnome-terminal, konsole, or xterm.")


def open_doplet_terminal(host: dict[str, Any], doplet: dict[str, Any]) -> dict[str, Any]:
    details = describe_doplet_terminal(host, doplet)
    if not details.get("supported"):
        raise ValueError(details.get("reason") or "Terminal launch is not supported for this host.")
    inner_command = str(details.get("inner_command") or "").strip()
    if not inner_command:
        raise ValueError("Terminal launch command is empty.")
    launcher = str(details.get("launcher") or "")
    transport = str(details.get("transport") or "")
    target = str(details.get("target") or "")
    if transport == "ssh":
        title = f"Doplet SSH: {target}"
    elif transport == "virsh-console":
        title = f"Doplet Console: {target}"
    else:
        title = str(details.get("title") or "Doplet terminal")
    try:
        if launcher == "windows-wsl":
            _launch_windows_terminal(host, inner_command, title=title, as_root=bool(details.get("requires_root")))
        elif launcher == "linux-local":
            _launch_linux_terminal(inner_command, title=title)
        else:
            raise ValueError(f"Unsupported terminal launcher: {launcher}")
    except OSError as exc:
        raise RuntimeError(f"Could not launch the Doplet terminal: {exc}") from exc
    details = dict(details)
    details.pop("inner_command", None)
    details["launched"] = True
    return details


def execute_plan(
    host: dict[str, Any],
    steps: list[dict[str, Any]],
    dry_run: bool = False,
    *,
    progress_callback: Any | None = None,
    should_cancel: Any | None = None,
) -> list[dict[str, Any]]:
    host_mode = host.get("mode") or host.get("host_mode") or ""
    if host_mode in {"remote-linux", "windows-remote", "windows-wsl-remote"} and host.get("bootstrap_auth") == "password-bootstrap" and not dry_run:
        return [
            {
                "title": "Remote execution blocked",
                "ok": False,
                "run_mode": "remote",
                "stderr": (
                    "Password bootstrap is still selected. Use the Setup connection packet for the first manual SSH login "
                    "from Computer A, then switch the host profile to SSH key already ready before a live automated run."
                ),
                "stdout": "",
                "command": "",
            }
        ]

    results: list[dict[str, Any]] = []
    total_steps = max(len(steps), 1)
    for index, step in enumerate(steps):
        if should_cancel and should_cancel():
            raise PlanCancelled("Task execution cancelled before the next step started.")
        command = (step.get("command") or "").strip()
        if not command:
            result = {"title": step.get("title", "Unnamed step"), "ok": True, "skipped": True, "reason": "No command"}
            results.append(result)
            if progress_callback:
                progress_callback(index + 1, total_steps, result, list(results))
            continue
        if dry_run:
            result = {
                "title": step.get("title", "Unnamed step"),
                "ok": True,
                "dry_run": True,
                "command": command,
                "run_mode": step.get("run_mode", "local"),
            }
            results.append(result)
            if progress_callback:
                progress_callback(index + 1, total_steps, result, list(results))
            continue

        timeout = int(step.get("timeout", 120))
        use_wsl = step.get("use_wsl") if "use_wsl" in step else None
        run_as_root = bool(step.get("run_as_root", False))
        if step.get("run_mode") == "remote":
            result = run_remote_command(host, command, timeout=timeout, use_wsl=use_wsl, run_as_root=run_as_root)
        else:
            result = run_host_local_command(host, command, timeout=timeout, use_wsl=use_wsl, run_as_root=run_as_root)
        result["title"] = step.get("title", "Unnamed step")
        result["run_mode"] = step.get("run_mode", "local")
        result["run_as_root"] = run_as_root
        results.append(result)
        if progress_callback:
            progress_callback(index + 1, total_steps, result, list(results))
        if not result.get("ok"):
            break
    return results


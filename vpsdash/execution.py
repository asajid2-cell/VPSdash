from __future__ import annotations

import base64
import platform
import re
import shlex
import shutil
import socket
import subprocess
import time
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


def _windows_path_to_wsl_path(path: str) -> str:
    raw = str(path or "").strip().replace("\\", "/")
    if not raw:
        return ""
    drive_match = re.match(r"^([A-Za-z]):/(.*)$", raw)
    if drive_match:
        drive = drive_match.group(1).lower()
        rest = drive_match.group(2)
        return f"/mnt/{drive}/{rest}"
    return raw


def _ssh_inner_command(bootstrap_user: str, primary_ip: str, *, private_key_path: str = "") -> str:
    if private_key_path:
        normalized_key = _windows_path_to_wsl_path(private_key_path)
        safe_key = shlex.quote(normalized_key)
        user_host = shlex.quote(f"{bootstrap_user}@{primary_ip}")
        safe_bootstrap_user = re.sub(r"[^A-Za-z0-9_.-]+", "-", bootstrap_user or "user")
        staged_key = f'"$HOME/.ssh/vpsdash-{safe_bootstrap_user}-key"'
        return (
            'mkdir -p "$HOME/.ssh" && '
            f'install -m 600 -- {safe_key} {staged_key} && '
            f'ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=12 '
            f'-o IdentitiesOnly=yes -i {staged_key} {user_host}'
        )
    parts = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=12",
    ]
    if private_key_path:
        parts.extend(["-o", "IdentitiesOnly=yes", "-i", private_key_path])
    parts.append(f"{bootstrap_user}@{primary_ip}")
    return " ".join(shlex.quote(part) for part in parts)


def _windows_native_ssh_command(bootstrap_user: str, host: str, port: int, *, private_key_path: str = "") -> str:
    parts = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=12",
        "-o",
        "IdentitiesOnly=yes",
        "-p",
        str(int(port)),
    ]
    if private_key_path:
        parts.extend(["-i", private_key_path])
    parts.append(f"{bootstrap_user}@{host}")
    return subprocess.list2cmdline(parts)


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


def _guest_ssh_is_reachable(host: dict[str, Any], ip_address: str) -> bool:
    ip_address = str(ip_address or "").strip()
    if not ip_address:
        return False
    command = (
        "python3 -c "
        + shlex.quote(
            "import socket,sys; "
            f"s=socket.socket(); s.settimeout(2); "
            f"rc=s.connect_ex(({ip_address!r}, 22)); "
            "s.close(); "
            "sys.exit(0 if rc == 0 else 1)"
        )
    )
    if is_remote_mode(host):
        result = run_remote_command(host, command, timeout=8, run_as_root=False)
    else:
        result = run_host_local_command(host, command, timeout=8, run_as_root=False)
    return bool(result.get("ok"))


def _run_virsh_probe(host: dict[str, Any], command: str, *, timeout: int = 15) -> dict[str, Any]:
    if is_remote_mode(host):
        result = run_remote_command(host, command, timeout=timeout, run_as_root=False)
        if not result.get("ok") and not str(result.get("stdout") or "").strip():
            result = run_remote_command(host, command, timeout=timeout, run_as_root=True)
        return result
    result = run_host_local_command(host, command, timeout=timeout, run_as_root=False)
    if not result.get("ok") and not str(result.get("stdout") or "").strip():
        result = run_host_local_command(host, command, timeout=timeout, run_as_root=True)
    return result


def _normalize_doplet_runtime_status(raw_state: str, current_status: str) -> str:
    state = str(raw_state or "").strip().lower()
    if not state:
        return "missing" if current_status not in {"draft", "planned", "deleted"} else current_status
    if "shut off" in state or "shutoff" in state:
        return "stopped"
    if "running" in state or "idle" in state or "in shutdown" in state or "blocked" in state:
        return "running"
    if "paused" in state or "suspended" in state:
        return "paused"
    if "crashed" in state:
        return "error"
    return current_status or state


def inspect_doplet_runtime(host: dict[str, Any], doplet: dict[str, Any]) -> dict[str, Any]:
    slug = str(doplet.get("slug") or "").strip()
    current_status = str(doplet.get("status") or "").strip().lower()
    if not slug:
        return {
            "exists": False,
            "raw_state": "",
            "status": current_status or "draft",
            "ip_addresses": [str(item).strip() for item in doplet.get("ip_addresses") or [] if str(item).strip()],
        }
    result = _run_virsh_probe(host, f"virsh domstate {shlex.quote(slug)} 2>/dev/null || true", timeout=15)
    raw_state = str(result.get("stdout") or "").strip()
    status = _normalize_doplet_runtime_status(raw_state, current_status)
    ip_addresses: list[str] = []
    if raw_state and status in {"running", "paused"}:
        try:
            ip_addresses = _discover_doplet_ip_addresses(host, slug)
        except Exception:
            ip_addresses = []
    return {
        "exists": bool(raw_state),
        "raw_state": raw_state,
        "status": status,
        "ip_addresses": ip_addresses,
    }


def _windows_local_proxy_port(doplet: dict[str, Any]) -> int:
    doplet_id = int(doplet.get("id") or 0)
    base_port = 22000 + doplet_id
    if 22000 <= base_port <= 65000:
        return base_port
    slug = str(doplet.get("slug") or "doplet")
    return 24000 + (sum(ord(ch) for ch in slug) % 30000)


def _windows_local_proxy_is_ready(host: dict[str, Any], port: int) -> bool:
    command = (
        "python3 -c "
        + shlex.quote(
            "import socket,sys; "
            f"s=socket.socket(); s.settimeout(1); "
            f"rc=s.connect_ex(('127.0.0.1', {int(port)})); "
            "s.close(); "
            "sys.exit(0 if rc == 0 else 1)"
        )
    )
    result = run_host_local_command(host, command, timeout=5, run_as_root=False)
    return bool(result.get("ok"))


def _windows_local_native_port_is_ready(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        return sock.connect_ex(("127.0.0.1", int(port))) == 0
    finally:
        sock.close()


def _ensure_windows_local_ssh_proxy(host: dict[str, Any], doplet: dict[str, Any], primary_ip: str) -> int:
    port = _windows_local_proxy_port(doplet)
    if _windows_local_proxy_is_ready(host, port) and _windows_local_native_port_is_ready(port):
        return port
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(doplet.get("slug") or "doplet")) or "doplet"
    runtime_root = "/tmp/vpsdash-ssh-proxy"
    script_path = f"{runtime_root}/{slug}-{port}.py"
    log_path = f"{runtime_root}/{slug}-{port}.log"
    pid_path = f"{runtime_root}/{slug}-{port}.pid"
    proxy_script = "\n".join(
        [
            "import socket, threading",
            f"LISTEN=('0.0.0.0', {int(port)})",
            f"TARGET=({primary_ip!r}, 22)",
            "server=socket.socket(socket.AF_INET, socket.SOCK_STREAM)",
            "server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)",
            "server.bind(LISTEN)",
            "server.listen(16)",
            "def pipe(src, dst):",
            "    try:",
            "        while True:",
            "            data = src.recv(65536)",
            "            if not data:",
            "                break",
            "            dst.sendall(data)",
            "    except Exception:",
            "        pass",
            "    finally:",
            "        try: src.close()",
            "        except Exception: pass",
            "        try: dst.close()",
            "        except Exception: pass",
            "while True:",
            "    client, _ = server.accept()",
            "    upstream = socket.create_connection(TARGET, timeout=10)",
            "    threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()",
            "    threading.Thread(target=pipe, args=(upstream, client), daemon=True).start()",
        ]
    )
    script_literal = shlex.quote(proxy_script)
    command = "\n".join(
        [
            f"mkdir -p {shlex.quote(runtime_root)}",
            f"if [ -f {shlex.quote(pid_path)} ]; then",
            f"  pid=$(cat {shlex.quote(pid_path)} 2>/dev/null || true)",
            f"  if [ -n \"$pid\" ] && kill -0 \"$pid\" 2>/dev/null; then kill \"$pid\" 2>/dev/null || true; fi",
            f"  rm -f {shlex.quote(pid_path)}",
            "fi",
            f"cat > {shlex.quote(script_path)} <<'PY'\n{proxy_script}\nPY",
            f"nohup python3 {shlex.quote(script_path)} > {shlex.quote(log_path)} 2>&1 < /dev/null &",
            f"echo $! > {shlex.quote(pid_path)}",
        ]
    )
    run_host_local_command(host, command, timeout=8, run_as_root=False)
    for _ in range(16):
        time.sleep(0.25)
        if _windows_local_proxy_is_ready(host, port) and _windows_local_native_port_is_ready(port):
            return port
    wsl_log = run_host_local_command(host, f"tail -n 40 {shlex.quote(log_path)} 2>/dev/null || true", timeout=5, run_as_root=False)
    detail = str(wsl_log.get("stdout") or wsl_log.get("stderr") or "").strip()
    raise RuntimeError(
        f"Could not start the Windows-local SSH bridge for {primary_ip}:22."
        + (f"\n\nBridge log:\n{detail}" if detail else "")
    )


def describe_doplet_terminal(
    host: dict[str, Any],
    doplet: dict[str, Any],
    *,
    establish_localhost_endpoint: bool = True,
) -> dict[str, Any]:
    host_mode = _host_mode(host)
    slug = str(doplet.get("slug") or "").strip()
    if not slug:
        raise ValueError("Doplet slug is required for terminal access.")
    name = str(doplet.get("name") or slug).strip() or slug
    status = str(doplet.get("status") or "draft").strip().lower()
    distro = str(host.get("wsl_distribution") or "Ubuntu").strip() or "Ubuntu"
    bootstrap_user = str(doplet.get("bootstrap_user") or "ubuntu").strip() or "ubuntu"
    metadata = dict(doplet.get("metadata_json") or {})
    ip_addresses = [str(item).strip() for item in doplet.get("ip_addresses") or [] if str(item).strip()]
    if status not in {"draft", "planned", "deleted"}:
        try:
            discovered_ips = _discover_doplet_ip_addresses(host, slug)
        except Exception:
            discovered_ips = []
        if discovered_ips:
            ip_addresses = discovered_ips
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
        local_private_key_path = str(metadata.get("local_private_key_path") or "").strip()
        access_note = ""
        if primary_ip.startswith("192.168.122.") or primary_ip.startswith("192.168.124."):
            access_note = "This guest IP is on the WSL/libvirt network. VPSdash will prefer a localhost SSH endpoint and fall back to the selected local WSL runtime if needed."
        ssh_ready = _guest_ssh_is_reachable(host, primary_ip) if primary_ip else False
        if primary_ip and ssh_ready:
            if establish_localhost_endpoint:
                try:
                    proxy_port = _ensure_windows_local_ssh_proxy(host, doplet, primary_ip)
                except Exception as exc:
                    inner_command = _ssh_inner_command(
                        bootstrap_user,
                        primary_ip,
                        private_key_path=local_private_key_path,
                    )
                    preview_command = build_wsl_command(host, inner_command)
                    metadata["access_bridge_error"] = str(exc)
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
                        "access_label": f"WSL SSH {bootstrap_user}@{primary_ip}",
                        "access_note": (access_note + " " if access_note else "") + "Localhost SSH endpoint is unavailable right now, so VPSdash is using direct WSL SSH.",
                        "status": status,
                    }
            else:
                proxy_port = _windows_local_proxy_port(doplet)
                if not (_windows_local_proxy_is_ready(host, proxy_port) and _windows_local_native_port_is_ready(proxy_port)):
                    inner_command = _ssh_inner_command(
                        bootstrap_user,
                        primary_ip,
                        private_key_path=local_private_key_path,
                    )
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
                        "access_label": f"WSL SSH {bootstrap_user}@{primary_ip}",
                        "access_note": (access_note + " " if access_note else "") + "Use Open Terminal to let VPSdash try a localhost endpoint, or run the WSL SSH command directly.",
                        "status": status,
                    }
            preview_command = _windows_native_ssh_command(
                bootstrap_user,
                "127.0.0.1",
                proxy_port,
                private_key_path=local_private_key_path,
            )
            return {
                "supported": True,
                "transport": "ssh",
                "launcher": "windows-native-ssh",
                "title": f"{name} terminal",
                "target": "127.0.0.1",
                "preview_command": preview_command,
                "bootstrap_user": bootstrap_user,
                "ip_addresses": ip_addresses,
                "wsl_distribution": distro,
                "reason": "",
                "inner_command": preview_command,
                "access_label": f"SSH {bootstrap_user}@127.0.0.1:{proxy_port}",
                "access_note": (access_note + " " if access_note else "") + f"Forwarded to guest IP {primary_ip}:22 from the selected WSL runtime.",
                "forward_host": "127.0.0.1",
                "forward_port": proxy_port,
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
            "access_note": (
                f"Guest IPs detected: {', '.join(ip_addresses)}. SSH is not reachable from the selected WSL runtime yet, so VPSdash is opening the serial console."
                if primary_ip
                else "No guest IP has been detected yet. Open the serial console until cloud-init networking and SSH are ready."
            ),
            "status": status,
        }

    if host_mode in LINUX_LOCAL_MODES:
        ssh_ready = _guest_ssh_is_reachable(host, primary_ip) if primary_ip else False
        if primary_ip and ssh_ready:
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
            "access_note": (
                f"Guest IPs detected: {', '.join(ip_addresses)}. SSH is not reachable from the local hypervisor yet, so VPSdash is opening the serial console."
                if primary_ip
                else ""
            ),
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


def _launch_windows_native_ssh_terminal(command: str, *, title: str) -> None:
    session_command = (
        f'$Host.UI.RawUI.WindowTitle = "{title.replace(chr(34), "")}"; '
        f"{command}; "
        'Write-Host ""; '
        'Read-Host "Press Enter to close"'
    )
    kwargs: dict[str, Any] = {}
    if platform.system().lower().startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen(["powershell.exe", "-NoExit", "-Command", session_command], **kwargs)


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
    details = describe_doplet_terminal(host, doplet, establish_localhost_endpoint=True)
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
        elif launcher == "windows-native-ssh":
            _launch_windows_native_ssh_terminal(inner_command, title=title)
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


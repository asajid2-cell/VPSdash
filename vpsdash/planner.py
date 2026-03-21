from __future__ import annotations

import copy
import uuid
from datetime import datetime
from typing import Any


def slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "project"


def ensure_id(value: str | None, prefix: str) -> str:
    if value:
        return value
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def merge_template(template: dict[str, Any], project: dict[str, Any] | None) -> dict[str, Any]:
    merged = copy.deepcopy(template)
    merged.pop("template_file", None)
    project = project or {}
    merged.update(project)
    merged["id"] = ensure_id(project.get("id"), "project")
    merged["name"] = project.get("name") or template.get("name") or "New Project"
    merged["slug"] = project.get("slug") or slugify(merged["name"])
    merged["env"] = project.get("env") or copy.deepcopy(template.get("env", []))
    merged["services"] = project.get("services") or copy.deepcopy(template.get("services", []))
    merged["persistent_paths"] = project.get("persistent_paths") or copy.deepcopy(template.get("persistent_paths", []))
    merged["backup_paths"] = project.get("backup_paths") or copy.deepcopy(template.get("backup_paths", []))
    merged["health_checks"] = project.get("health_checks") or copy.deepcopy(template.get("health_checks", []))
    merged["domains"] = project.get("domains") or copy.deepcopy(template.get("domains", []))
    merged["build_steps"] = project.get("build_steps") or copy.deepcopy(template.get("build_steps", []))
    return merged


def default_host_shell(host: dict[str, Any]) -> str:
    return "powershell" if host.get("mode") in {"windows-local", "windows-remote", "windows-wsl-remote"} else "bash"


def make_step(
    title: str,
    command: str,
    run_mode: str,
    *,
    detail: str = "",
    risky: bool = False,
    timeout: int = 120,
    use_wsl: bool | None = None,
) -> dict[str, Any]:
    step = {
        "title": title,
        "detail": detail,
        "command": command,
        "run_mode": run_mode,
        "risky": risky,
        "timeout": timeout,
    }
    if use_wsl is not None:
        step["use_wsl"] = use_wsl
    return step


def _run_mode(host: dict[str, Any]) -> str:
    return "remote" if host.get("mode") in {"remote-linux", "windows-remote", "windows-wsl-remote"} else "local"


def _as_host_linux_command(host: dict[str, Any], command: str) -> str:
    return command


def _remote_prereq_steps(host: dict[str, Any]) -> list[dict[str, Any]]:
    if host.get("mode") not in {"remote-linux", "windows-remote", "windows-wsl-remote"}:
        return []
    steps: list[dict[str, Any]] = []
    if host.get("mode") in {"windows-remote", "windows-wsl-remote"}:
        distro = host.get("wsl_distribution") or "Ubuntu"
        escaped_distro = str(distro).replace("'", "''")
        steps.extend(
            [
                make_step(
                    "Check remote WSL distros",
                    'powershell -NoProfile -Command "wsl -l -v"',
                    "remote",
                    detail="Inspect the remote Windows machine before Linux-side provisioning starts.",
                    timeout=30,
                    use_wsl=False,
                ),
                make_step(
                    "Install remote WSL distro if missing",
                    (
                        'powershell -NoProfile -Command '
                        f'"$d=\'{escaped_distro}\'; '
                        '$names=@(wsl -l -q 2>$null | ForEach-Object { $_.Trim() } | Where-Object { $_ }); '
                        'if ($names -notcontains $d) { wsl --install -d $d }"'
                    ),
                    "remote",
                    detail="Ensure the remote Windows host has the selected WSL distro before Linux-side automation continues.",
                    risky=True,
                    timeout=2400,
                    use_wsl=False,
                ),
            ]
        )
    steps.extend(
        [
        make_step("Refresh package index", "sudo apt update", "remote", detail="Refresh package metadata before setup."),
        make_step(
            "Install base packages",
            "sudo apt install -y curl wget git ca-certificates gnupg lsb-release ufw nginx certbot python3-certbot-nginx",
            "remote",
            detail="Install the base operations toolchain and reverse proxy.",
            timeout=600,
        ),
        make_step(
            "Install Docker Engine",
            "if ! command -v docker >/dev/null 2>&1; then "
            "sudo install -m 0755 -d /etc/apt/keyrings && "
            "curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg && "
            "echo \"deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable\" | "
            "sudo tee /etc/apt/sources.list.d/docker.list >/dev/null && "
            "sudo apt update && sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; "
            "fi",
            "remote",
            detail="Install Docker and Compose only if missing.",
            risky=True,
            timeout=900,
        ),
        make_step(
            "Ensure 4G swap exists",
            "if ! swapon --show | grep -q /swapfile; then "
            "sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile && "
            "grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab; "
            "fi",
            "remote",
            detail="Provision swap to reduce OOM pressure on smaller hosts.",
            risky=True,
            timeout=300,
        ),
        ]
    )
    return steps


def _windows_prereq_steps(host: dict[str, Any]) -> list[dict[str, Any]]:
    if host.get("mode") != "windows-local":
        return []
    distro = host.get("wsl_distribution") or "Ubuntu"
    return [
        make_step(
            "Check WSL distros",
            "powershell -NoProfile -Command \"wsl -l -v\"",
            "local",
            detail="Inspect the installed WSL distributions and versions.",
            use_wsl=False,
        ),
        make_step(
            "Install WSL distro if missing",
            f'powershell -NoProfile -Command "wsl -l -q | findstr /R /C:\\"^{distro}$\\" || wsl --install -d {distro}"',
            "local",
            detail="Install the target Linux distribution if it is not present yet.",
            risky=True,
            timeout=1800,
            use_wsl=False,
        ),
        make_step(
            "Install Linux host packages inside WSL",
            _as_host_linux_command(
                host,
                "sudo apt update && sudo apt install -y curl wget git ca-certificates gnupg lsb-release ufw nginx certbot python3-certbot-nginx docker.io docker-compose-plugin",
            ),
            "local",
            detail="Prepare the WSL environment as the host runtime.",
            risky=True,
            timeout=1800,
        ),
    ]


def _repo_steps(host: dict[str, Any], project: dict[str, Any]) -> list[dict[str, Any]]:
    deploy_path = project.get("deploy_path") or f"~/apps/{project['slug']}"
    base_dir = deploy_path.rsplit("/", 1)[0] if "/" in deploy_path else deploy_path
    repo_url = project.get("repo_url") or ""
    branch = project.get("branch") or "main"
    run_mode = _run_mode(host)

    steps = [make_step("Create deploy directory", _as_host_linux_command(host, f"mkdir -p {base_dir}"), run_mode, detail="Prepare the parent path for the project checkout.")]
    if repo_url:
        steps.append(
            make_step(
                "Clone repository if needed",
                _as_host_linux_command(host, f"if [ ! -d {deploy_path}/.git ]; then git clone {repo_url} {deploy_path}; fi"),
                run_mode,
                detail="Clone the selected repo on first setup.",
                timeout=900,
            )
        )
    steps.append(
        make_step(
            "Sync repository to target branch",
            _as_host_linux_command(host, f"cd {deploy_path} && git fetch origin {branch} && git checkout {branch} && git pull origin {branch}"),
            run_mode,
            detail="Move the deployment to the selected branch.",
            timeout=600,
        )
    )
    return steps


def _storage_steps(host: dict[str, Any], project: dict[str, Any]) -> list[dict[str, Any]]:
    deploy_path = project.get("deploy_path") or f"~/apps/{project['slug']}"
    run_mode = _run_mode(host)
    paths = project.get("persistent_paths", [])
    if not paths:
        return []
    joined = " ".join(f"{deploy_path}/{path}" for path in paths)
    return [make_step("Create persistent directories", _as_host_linux_command(host, f"mkdir -p {joined}"), run_mode, detail="Create the data paths that must survive rebuilds.")]


def _env_steps(host: dict[str, Any], project: dict[str, Any]) -> list[dict[str, Any]]:
    deploy_path = project.get("deploy_path") or f"~/apps/{project['slug']}"
    run_mode = _run_mode(host)
    env_lines = []
    for item in project.get("env", []):
        key = item.get("key")
        if not key:
            continue
        value = str(item.get("value", "")).replace('"', '\\"')
        env_lines.append(f'{key}="{value}"')
    if not env_lines:
        return []
    env_body = "\\n".join(env_lines)
    return [
        make_step(
            "Write environment file",
            _as_host_linux_command(host, f"cd {deploy_path} && printf '{env_body}\\n' > .env"),
            run_mode,
            detail="Render the collected environment variables into the project .env file.",
            risky=True,
        )
    ]


def _build_steps(host: dict[str, Any], project: dict[str, Any]) -> list[dict[str, Any]]:
    deploy_path = project.get("deploy_path") or f"~/apps/{project['slug']}"
    run_mode = _run_mode(host)
    steps: list[dict[str, Any]] = []
    for build_step in project.get("build_steps", []):
        command = build_step.get("command", "")
        if not command:
            continue
        steps.append(
            make_step(
                build_step.get("title", "Build step"),
                _as_host_linux_command(host, f"cd {deploy_path} && {command}"),
                run_mode,
                detail=build_step.get("detail", ""),
                timeout=int(build_step.get("timeout", 1200)),
            )
        )
    return steps


def _nginx_steps(host: dict[str, Any], project: dict[str, Any]) -> list[dict[str, Any]]:
    if host.get("mode") not in {"remote-linux", "windows-remote", "windows-wsl-remote", "linux-local", "windows-local"}:
        return []
    run_mode = _run_mode(host)
    primary_domain = project.get("primary_domain") or ""
    domains = [primary_domain, *project.get("domains", [])]
    domains = [domain for domain in domains if domain]
    if not domains:
        return []

    services = project.get("services") or [{}]
    proxy_port = str(project.get("proxy_target_port") or services[0].get("public_port") or 5000)
    conf_name = project.get("nginx_site_name") or f"{project['slug']}.conf"
    server_names = " ".join(domains)
    certificate_domain = domains[0]
    nginx_conf = (
        f"server {{\n"
        f"    listen 80;\n"
        f"    listen [::]:80;\n"
        f"    server_name {server_names};\n"
        f"    return 301 https://$host$request_uri;\n"
        f"}}\n\n"
        f"server {{\n"
        f"    listen 443 ssl;\n"
        f"    listen [::]:443 ssl;\n"
        f"    server_name {server_names};\n"
        f"    ssl_certificate /etc/letsencrypt/live/{certificate_domain}/fullchain.pem;\n"
        f"    ssl_certificate_key /etc/letsencrypt/live/{certificate_domain}/privkey.pem;\n"
        f"    client_max_body_size 200m;\n"
        f"    location / {{\n"
        f"        proxy_pass http://127.0.0.1:{proxy_port};\n"
        f"        proxy_set_header Host $host;\n"
        f"        proxy_set_header X-Real-IP $remote_addr;\n"
        f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        f"        proxy_set_header X-Forwarded-Proto $scheme;\n"
        f"        proxy_read_timeout 600s;\n"
        f"        proxy_send_timeout 600s;\n"
        f"        client_body_timeout 600s;\n"
        f"    }}\n"
        f"}}\n"
    )
    return [
        make_step(
            "Install Nginx site config",
            _as_host_linux_command(
                host,
                f"sudo tee /etc/nginx/sites-available/{conf_name} >/dev/null <<'EOF'\n{nginx_conf}EOF\n"
                f"sudo ln -sf /etc/nginx/sites-available/{conf_name} /etc/nginx/sites-enabled/{conf_name}",
            ),
            run_mode,
            detail="Write and enable an Nginx site definition for the project domains.",
            risky=True,
            timeout=120,
        ),
        make_step("Validate Nginx", _as_host_linux_command(host, "sudo nginx -t"), run_mode, detail="Verify the generated reverse proxy config before reload."),
        make_step("Reload Nginx", _as_host_linux_command(host, "sudo systemctl reload nginx"), run_mode, detail="Apply the reverse proxy changes.", risky=True),
        make_step(
            "Request TLS certificates",
            _as_host_linux_command(
                host,
                f"sudo certbot --nginx {' '.join(f'-d {domain}' for domain in domains)} --non-interactive --agree-tos -m {project.get('letsencrypt_email', 'admin@example.com')} --redirect",
            ),
            run_mode,
            detail="Obtain and install Let's Encrypt certificates for the selected domains.",
            risky=True,
            timeout=900,
        ),
    ]


def _deploy_steps(host: dict[str, Any], project: dict[str, Any]) -> list[dict[str, Any]]:
    deploy_path = project.get("deploy_path") or f"~/apps/{project['slug']}"
    run_mode = _run_mode(host)
    return [
        make_step(
            "Rebuild and start containers",
            _as_host_linux_command(host, f"cd {deploy_path} && docker compose down && docker compose up -d --build"),
            run_mode,
            detail="Run the compose deployment lifecycle.",
            risky=True,
            timeout=1800,
        )
    ]


def _backup_steps(host: dict[str, Any], project: dict[str, Any]) -> list[dict[str, Any]]:
    deploy_path = project.get("deploy_path") or f"~/apps/{project['slug']}"
    backup_paths = project.get("backup_paths", [])
    if not backup_paths:
        return []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = f"{project['slug']}_{timestamp}.tar.gz"
    archive_path = f"~/backups/{archive_name}"
    joined = " ".join(f"{deploy_path}/{path}" for path in backup_paths)
    step = make_step(
        "Create backup archive",
        _as_host_linux_command(host, f"mkdir -p ~/backups && tar -czf {archive_path} {joined} {deploy_path}/.env && printf '{archive_path}\\n'"),
        _run_mode(host),
        detail="Generate an app backup containing data and environment state.",
        risky=True,
        timeout=900,
    )
    step["artifact_path"] = archive_path
    return [step]


def _verify_steps(host: dict[str, Any], project: dict[str, Any]) -> list[dict[str, Any]]:
    run_mode = _run_mode(host)
    steps: list[dict[str, Any]] = []
    for health_check in project.get("health_checks", []):
        command = health_check.get("command")
        if not command:
            continue
        steps.append(
            make_step(
                health_check.get("title", "Health check"),
                _as_host_linux_command(host, command),
                run_mode,
                detail=health_check.get("detail", ""),
                timeout=int(health_check.get("timeout", 60)),
            )
        )
    return steps


def generate_plan(host: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    host = copy.deepcopy(host)
    project = copy.deepcopy(project)
    host["id"] = ensure_id(host.get("id"), "host")
    project["id"] = ensure_id(project.get("id"), "project")

    warnings: list[str] = []
    if host.get("mode") in {"remote-linux", "windows-remote", "windows-wsl-remote"} and not host.get("ssh_host"):
        warnings.append("Remote host mode requires an SSH hostname or IP address.")
    if host.get("mode") in {"remote-linux", "windows-remote", "windows-wsl-remote"} and not host.get("ssh_user"):
        warnings.append("Remote host mode requires an SSH user before Computer A can reach Computer B.")
    if host.get("mode") in {"remote-linux", "windows-remote", "windows-wsl-remote"} and host.get("bootstrap_auth") == "password-bootstrap":
        warnings.append(
            "Password bootstrap is still selected. Use the connection packet from Setup for the first manual SSH login from Computer A, then switch to SSH key mode before automated remote runs."
        )
    if host.get("mode") in {"remote-linux", "windows-remote", "windows-wsl-remote"} and host.get("bootstrap_auth") == "ssh-key-ready" and not host.get("ssh_key_path"):
        warnings.append("SSH key mode is selected, but no SSH key path is configured on the control machine.")
    if not project.get("repo_url"):
        warnings.append("No repository URL is configured yet.")
    if host.get("mode") in {"windows-local", "windows-remote", "windows-wsl-remote"}:
        warnings.append("Windows hosting relies on WSL2, and availability still depends on the Windows machine staying awake.")
    if not project.get("primary_domain"):
        warnings.append("No primary domain is configured. TLS and Nginx generation will stay incomplete until a domain is supplied.")

    return {
        "summary": {
            "host_name": host.get("name") or "Unnamed host",
            "project_name": project.get("name") or "Unnamed project",
            "host_mode": host.get("mode", "remote-linux"),
            "device_role": host.get("device_role", "computer-a-main"),
            "bootstrap_auth": host.get("bootstrap_auth", "password-bootstrap"),
            "deploy_path": project.get("deploy_path") or f"~/apps/{project['slug']}",
            "shell": default_host_shell(host),
        },
        "warnings": warnings,
        "stages": [
            {"id": "prerequisites", "title": "Host prerequisites", "steps": _remote_prereq_steps(host) + _windows_prereq_steps(host)},
            {"id": "repository", "title": "Repository and branch", "steps": _repo_steps(host, project)},
            {"id": "storage", "title": "Persistent storage", "steps": _storage_steps(host, project)},
            {"id": "environment", "title": "Environment", "steps": _env_steps(host, project)},
            {"id": "build", "title": "Build steps", "steps": _build_steps(host, project)},
            {"id": "proxy", "title": "Proxy and TLS", "steps": _nginx_steps(host, project)},
            {"id": "deploy", "title": "Deploy stack", "steps": _deploy_steps(host, project)},
            {"id": "backup", "title": "Backups", "steps": _backup_steps(host, project)},
            {"id": "verify", "title": "Verification", "steps": _verify_steps(host, project)},
        ],
    }

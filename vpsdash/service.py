from __future__ import annotations

import getpass
import os
import platform
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .diagnostics import run_diagnostics, run_monitor_snapshot
from .execution import execute_plan
from .platform_service import LoginResult, PlatformService
from .planner import ensure_id, generate_plan, merge_template
from .state import StateStore
from .template_loader import DefaultCatalog, TemplateCatalog


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_host(payload: dict) -> dict:
    host = dict(payload or {})
    host["id"] = ensure_id(host.get("id"), "host")
    host["name"] = host.get("name") or "New host"
    host["mode"] = host.get("mode") or "remote-linux"
    host["ssh_port"] = int(host.get("ssh_port") or 22)
    host["strict_host_key_checking"] = host.get("strict_host_key_checking") or "accept-new"
    host["wsl_distribution"] = host.get("wsl_distribution") or "Ubuntu"
    host["device_role"] = host.get("device_role") or "computer-a-main"
    host["bootstrap_auth"] = host.get("bootstrap_auth") or "password-bootstrap"
    return host


def _normalize_project(payload: dict, catalog: TemplateCatalog) -> dict:
    project = dict(payload or {})
    template_id = project.get("template_id") or "generic-docker-webapp"
    template = catalog.get_template(template_id)
    merged = merge_template(template, project)
    merged["template_id"] = template_id
    merged["repo_url"] = merged.get("repo_url") or ""
    merged["branch"] = merged.get("branch") or "main"
    merged["deploy_path"] = merged.get("deploy_path") or f"~/apps/{merged['slug']}"
    merged["primary_domain"] = merged.get("primary_domain") or ""
    merged["letsencrypt_email"] = merged.get("letsencrypt_email") or "admin@example.com"
    return merged


def host_key_candidates() -> list[str]:
    home = Path.home()
    candidates = [home / ".ssh" / "id_ed25519", home / ".ssh" / "id_rsa"]
    return [str(path) for path in candidates if path.exists()]


def public_key_candidates() -> list[dict[str, str]]:
    home = Path.home()
    ssh_dir = home / ".ssh"
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in [ssh_dir / "id_ed25519.pub", ssh_dir / "id_rsa.pub", *sorted(ssh_dir.glob("*.pub"))]:
        try:
            resolved = str(path.resolve())
        except OSError:
            resolved = str(path)
        if resolved in seen or not path.exists() or not path.is_file():
            continue
        seen.add(resolved)
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not value:
            continue
        entries.append(
            {
                "path": resolved,
                "private_key_path": resolved[:-4] if resolved.lower().endswith(".pub") else resolved,
                "label": path.name,
                "public_key": value,
            }
        )
    return entries


def _list_wsl_distributions() -> list[str]:
    if os.name != "nt":
        return []
    startup_kwargs: dict[str, Any] = {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        startup_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup_kwargs["startupinfo"] = startupinfo
    try:
        result = subprocess.run(
            ["wsl.exe", "-l", "-q"],
            capture_output=True,
            timeout=8,
            check=False,
            **startup_kwargs,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    stdout = result.stdout if isinstance(result.stdout, bytes) else str(result.stdout or "").encode("utf-8", errors="ignore")
    decoded = ""
    for encoding in ("utf-16", "utf-16-le", "utf-8", "cp1252"):
        try:
            decoded = stdout.decode(encoding).replace("\x00", "").strip()
        except UnicodeDecodeError:
            continue
        if decoded:
            break
    if not decoded:
        decoded = stdout.decode("utf-8", errors="ignore").replace("\x00", "").strip()
    return [line.strip() for line in decoded.splitlines() if line.strip()]


def local_machine_info() -> dict[str, Any]:
    hostname = socket.gethostname()
    fqdn = socket.getfqdn()
    ip_candidates: list[str] = []
    seen: set[str] = set()

    try:
        for addrinfo in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            ip = addrinfo[4][0]
            if ip and ip not in seen and not ip.startswith("127."):
                ip_candidates.append(ip)
                seen.add(ip)
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and ip not in seen and not ip.startswith("127."):
                ip_candidates.insert(0, ip)
                seen.add(ip)
    except OSError:
        pass

    wsl_distributions = _list_wsl_distributions()
    preferred_wsl = next((item for item in wsl_distributions if item.lower() == "ubuntu"), None) or (
        wsl_distributions[0] if wsl_distributions else "Ubuntu"
    )
    ssh_targets = [item for item in [hostname, fqdn, *ip_candidates] if item]

    return {
        "hostname": hostname,
        "fqdn": fqdn,
        "username": getpass.getuser(),
        "platform": f"{platform.system()} {platform.release()}".strip(),
        "ip_candidates": ip_candidates,
        "ssh_targets": ssh_targets,
        "ssh_public_keys": public_key_candidates(),
        "wsl_distributions": wsl_distributions,
        "recommended_wsl_distribution": preferred_wsl,
    }


def _template_builtin_defaults(
    catalog: TemplateCatalog,
    *,
    exclude_templates: set[str] | None = None,
) -> list[dict[str, Any]]:
    exclude_templates = exclude_templates or set()
    defaults: list[dict[str, Any]] = []
    for template in catalog.list_templates():
        if template["id"] in exclude_templates:
            continue
        defaults.append(
            {
                "id": f"builtin-{template['id']}",
                "name": template.get("name") or "Builtin default",
                "description": template.get("description") or "Built-in setup default.",
                "kind": "builtin",
                "source_template_id": template["id"],
                "host_defaults": {
                    "name": "",
                    "mode": "remote-linux",
                    "ssh_user": "",
                    "ssh_host": "",
                    "ssh_port": 22,
                    "ssh_key_path": "",
                    "wsl_distribution": "Ubuntu",
                    "device_role": "computer-a-main",
                    "bootstrap_auth": "password-bootstrap",
                },
                "project_defaults": {
                    **template,
                    "id": None,
                    "template_id": template["id"],
                },
            }
        )
    return defaults


def _normalize_builtin_default(payload: dict, catalog: TemplateCatalog) -> dict[str, Any]:
    default_profile = dict(payload or {})
    template_id = (
        default_profile.get("source_template_id")
        or (default_profile.get("project_defaults") or {}).get("template_id")
        or "generic-docker-webapp"
    )

    host_defaults = _normalize_host(default_profile.get("host_defaults") or {})
    host_defaults.pop("id", None)

    project_defaults = dict(default_profile.get("project_defaults") or {})
    project_defaults["template_id"] = project_defaults.get("template_id") or template_id
    project_defaults = _normalize_project(project_defaults, catalog)
    project_defaults.pop("id", None)

    return {
        "id": ensure_id(default_profile.get("id"), "builtin-default"),
        "name": default_profile.get("name") or project_defaults.get("name") or "Builtin default",
        "description": default_profile.get("description") or "Built-in setup default.",
        "kind": "builtin",
        "source_template_id": project_defaults.get("template_id"),
        "host_defaults": host_defaults,
        "project_defaults": project_defaults,
    }


def _builtin_defaults(catalog: TemplateCatalog, default_catalog: DefaultCatalog) -> list[dict[str, Any]]:
    file_defaults = [_normalize_builtin_default(item, catalog) for item in default_catalog.list_defaults()]
    covered_templates = {
        str(item.get("source_template_id"))
        for item in file_defaults
        if item.get("source_template_id")
    }
    return file_defaults + _template_builtin_defaults(catalog, exclude_templates=covered_templates)


def _normalize_default(payload: dict, catalog: TemplateCatalog) -> dict[str, Any]:
    default_profile = dict(payload or {})
    default_profile["id"] = ensure_id(default_profile.get("id"), "default")
    default_profile["name"] = default_profile.get("name") or "New setup default"
    default_profile["description"] = default_profile.get("description") or "Custom combined host and project preset."
    default_profile["kind"] = "custom"

    host_defaults = _normalize_host(default_profile.get("host_defaults") or {})
    host_defaults.pop("id", None)

    project_defaults = dict(default_profile.get("project_defaults") or {})
    project_defaults["template_id"] = project_defaults.get("template_id") or default_profile.get("source_template_id") or "generic-docker-webapp"
    project_defaults = _normalize_project(project_defaults, catalog)
    project_defaults.pop("id", None)

    return {
        "id": default_profile["id"],
        "name": default_profile["name"],
        "description": default_profile["description"],
        "kind": "custom",
        "source_template_id": project_defaults.get("template_id"),
        "host_defaults": host_defaults,
        "project_defaults": project_defaults,
    }


def _normalize_backup_entry(payload: dict) -> dict[str, Any]:
    backup = dict(payload or {})
    backup["id"] = ensure_id(backup.get("id"), "backup")
    backup["created_at"] = backup.get("created_at") or _now_iso()
    backup["title"] = backup.get("title") or "Create backup archive"
    backup["status"] = backup.get("status") or ("ok" if backup.get("ok") else "failed")
    backup["artifact_path"] = backup.get("artifact_path") or ""
    backup["stdout"] = backup.get("stdout") or ""
    backup["stderr"] = backup.get("stderr") or ""
    backup["command"] = backup.get("command") or ""
    return backup


def _normalize_instance(payload: dict, catalog: TemplateCatalog) -> dict[str, Any]:
    instance = dict(payload or {})
    host = _normalize_host(instance.get("host") or {})
    project = _normalize_project(instance.get("project") or {}, catalog)
    now = _now_iso()
    backups = [_normalize_backup_entry(item) for item in instance.get("backups", [])]
    created_at = instance.get("created_at") or now
    return {
        "id": ensure_id(instance.get("id"), "instance"),
        "name": instance.get("name") or f"{project.get('name', 'Project')} on {host.get('name', 'Host')}",
        "description": instance.get("description") or "Managed deployment instance.",
        "host_id": instance.get("host_id") or host.get("id"),
        "project_id": instance.get("project_id") or project.get("id"),
        "host": host,
        "project": project,
        "backups": backups,
        "created_at": created_at,
        "updated_at": now,
    }


class VpsDashService:
    def __init__(self, root: Path | str, *, resource_root: Path | str | None = None) -> None:
        self.root = Path(root)
        self.resource_root = Path(resource_root) if resource_root else self.root
        self.store = StateStore(self.root)
        self.catalog = TemplateCatalog(self.resource_root)
        self.default_catalog = DefaultCatalog(self.resource_root)
        self.platform = PlatformService(self.root)

    def bootstrap(self) -> dict[str, Any]:
        state = self.store.read()
        return {
            "templates": self.catalog.list_templates(),
            "defaults": _builtin_defaults(self.catalog, self.default_catalog) + state.get("defaults", []),
            "state": state,
            "key_candidates": host_key_candidates(),
            "platform": os.name,
            "local_machine": local_machine_info(),
            "control_plane": self.platform.bootstrap(),
        }

    def local_machine(self) -> dict[str, Any]:
        return local_machine_info()

    def list_templates(self) -> list[dict[str, Any]]:
        return self.catalog.list_templates()

    def read_state(self) -> dict[str, Any]:
        return self.store.read()

    def upsert_default(self, payload: dict) -> dict[str, Any]:
        default_profile = _normalize_default(payload, self.catalog)
        state = self.store.upsert_default(default_profile)
        return {"default": default_profile, "state": state}

    def upsert_host(self, payload: dict) -> dict[str, Any]:
        host = _normalize_host(payload)
        state = self.store.upsert_host(host)
        return {"host": host, "state": state}

    def upsert_project(self, payload: dict) -> dict[str, Any]:
        project = _normalize_project(payload, self.catalog)
        state = self.store.upsert_project(project)
        return {"project": project, "state": state}

    def upsert_instance(self, payload: dict) -> dict[str, Any]:
        instance = _normalize_instance(payload, self.catalog)
        state = self.store.upsert_instance(instance)
        return {"instance": instance, "state": state}

    def delete_instance(self, instance_id: str) -> dict[str, Any]:
        state = self.store.delete_instance(instance_id)
        return {"state": state}

    def create_instance_backup(self, instance_id: str) -> dict[str, Any]:
        state = self.store.read()
        instance = next((item for item in state.get("instances", []) if item.get("id") == instance_id), None)
        if not instance:
            raise KeyError(f"Unknown instance: {instance_id}")

        normalized_instance = _normalize_instance(instance, self.catalog)
        plan = generate_plan(normalized_instance["host"], normalized_instance["project"])
        backup_steps = next((stage.get("steps", []) for stage in plan.get("stages", []) if stage.get("id") == "backup"), [])
        if not backup_steps:
            raise ValueError("This instance has no configured backup paths.")

        results = execute_plan(normalized_instance["host"], backup_steps, dry_run=False)
        first_result = results[0] if results else {}
        backup_entry = _normalize_backup_entry(
            {
                "title": first_result.get("title") or "Create backup archive",
                "status": "ok" if all(item.get("ok") for item in results) else "failed",
                "ok": all(item.get("ok") for item in results),
                "artifact_path": backup_steps[0].get("artifact_path", ""),
                "stdout": first_result.get("stdout", ""),
                "stderr": first_result.get("stderr", ""),
                "command": first_result.get("command", backup_steps[0].get("command", "")),
            }
        )

        normalized_instance["backups"] = [backup_entry, *normalized_instance.get("backups", [])]
        normalized_instance["updated_at"] = _now_iso()
        state = self.store.upsert_instance(normalized_instance)
        return {"instance": normalized_instance, "backup": backup_entry, "results": results, "state": state}

    def generate_plan(self, host_payload: dict, project_payload: dict) -> dict[str, Any]:
        host = _normalize_host(host_payload)
        project = _normalize_project(project_payload, self.catalog)
        plan = generate_plan(host, project)
        return {"host": host, "project": project, "plan": plan}

    def diagnostics(self, host_payload: dict, project_payload: dict | None = None) -> dict[str, Any]:
        host = _normalize_host(host_payload)
        project = _normalize_project(project_payload, self.catalog) if project_payload else None
        return run_diagnostics(host, project)

    def monitor_snapshot(self, host_payload: dict, project_payload: dict | None = None) -> dict[str, Any]:
        host = _normalize_host(host_payload)
        project = _normalize_project(project_payload, self.catalog) if project_payload else None
        return run_monitor_snapshot(host, project)

    def execute(self, host_payload: dict, steps: list[dict[str, Any]], dry_run: bool) -> dict[str, Any]:
        host = _normalize_host(host_payload)
        return {"results": execute_plan(host, steps, dry_run=dry_run)}

    def create_user(self, payload: dict, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.create_user(payload, actor=actor)

    def authenticate_user(self, username_or_email: str, password: str, device_payload: dict, *, actor: str = "web") -> LoginResult:
        return self.platform.authenticate_user(username_or_email, password, device_payload, actor=actor)

    def verify_login_challenge(self, challenge_id: int, code: str, device_payload: dict, *, actor: str = "web") -> LoginResult:
        return self.platform.verify_login_challenge(challenge_id, code, device_payload, actor=actor)

    def verify_trusted_device(self, user_id: int, fingerprint_source: str, trust_token: str) -> bool:
        return self.platform.verify_trusted_device(user_id, fingerprint_source, trust_token)

    def list_users(self) -> list[dict[str, Any]]:
        return self.platform.list_users()

    def get_platform_user(self, user_id: int) -> dict[str, Any] | None:
        return self.platform.get_user(user_id)

    def upsert_platform_host(self, payload: dict, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.upsert_host(payload, actor=actor)

    def capture_platform_host_inventory(self, host_id: int, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.capture_host_inventory(host_id, actor=actor)

    def queue_prepare_platform_host(self, host_id: int, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.queue_prepare_host(host_id, actor=actor)

    def upsert_network(self, payload: dict, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.upsert_network(payload, actor=actor)

    def queue_network_apply(self, network_id: int, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.queue_apply_network(network_id, actor=actor)

    def queue_network_delete_runtime(self, network_id: int, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.queue_delete_network_runtime(network_id, actor=actor)

    def upsert_backup_provider(self, payload: dict, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.upsert_backup_provider(payload, actor=actor)

    def test_backup_provider(self, provider_id: int) -> dict[str, Any]:
        return self.platform.test_backup_provider(provider_id)

    def upsert_doplet(self, payload: dict, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.upsert_doplet(payload, actor=actor)

    def queue_doplet_create(self, doplet_id: int, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.queue_doplet_create(doplet_id, actor=actor)

    def queue_doplet_lifecycle(self, doplet_id: int, action: str, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.queue_doplet_lifecycle(doplet_id, action, actor=actor)

    def open_doplet_terminal(self, doplet_id: int, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.open_doplet_terminal(doplet_id, actor=actor)

    def describe_doplet_terminal(self, doplet_id: int, *, establish_localhost_endpoint: bool = True) -> dict[str, Any]:
        return self.platform.describe_doplet_terminal(doplet_id, establish_localhost_endpoint=establish_localhost_endpoint)

    def queue_doplet_resize(self, doplet_id: int, payload: dict[str, Any] | None = None, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.queue_resize_doplet(doplet_id, payload or {}, actor=actor)

    def queue_doplet_backup(self, doplet_id: int, provider_ids: list[int] | None = None, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.queue_backup(doplet_id, provider_ids=provider_ids, actor=actor)

    def queue_doplet_snapshot(self, doplet_id: int, snapshot_name: str | None = None, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.queue_snapshot(doplet_id, snapshot_name=snapshot_name, actor=actor)

    def queue_doplet_clone(self, doplet_id: int, payload: dict[str, Any] | None = None, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.queue_clone(doplet_id, payload or {}, actor=actor)

    def queue_snapshot_restore(self, snapshot_id: int, payload: dict[str, Any] | None = None, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.queue_restore_snapshot(snapshot_id, payload or {}, actor=actor)

    def delete_platform_doplet(self, doplet_id: int, *, actor: str = "system") -> None:
        self.platform.delete_doplet(doplet_id, actor=actor)

    def force_delete_platform_doplet(self, doplet_id: int, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.force_delete_doplet(doplet_id, actor=actor)

    def delete_platform_host(self, host_id: int, *, actor: str = "system") -> None:
        self.platform.delete_host(host_id, actor=actor)

    def delete_platform_network(self, network_id: int, *, actor: str = "system") -> None:
        self.platform.delete_network(network_id, actor=actor)

    def delete_platform_backup_provider(self, provider_id: int, *, actor: str = "system") -> None:
        self.platform.delete_backup_provider(provider_id, actor=actor)

    def run_platform_task(self, task_id: int, *, actor: str = "system", dry_run: bool = False) -> dict[str, Any]:
        return self.platform.run_task(task_id, actor=actor, dry_run=dry_run)

    def launch_platform_task(self, task_id: int, *, actor: str = "system", dry_run: bool = False) -> dict[str, Any]:
        return self.platform.launch_task(task_id, actor=actor, dry_run=dry_run)

    def cancel_platform_task(self, task_id: int, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.cancel_task(task_id, actor=actor)

    def retry_platform_task(self, task_id: int, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.retry_task(task_id, actor=actor)

    def run_backup_scheduler(self, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.run_backup_scheduler(actor=actor)

    def prune_platform_backups(self, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.prune_backup_records(actor=actor)

    def verify_platform_backup(self, backup_id: int, *, actor: str = "system") -> dict[str, Any]:
        return self.platform.verify_backup_record(backup_id, actor=actor)

    def host_acceptance_report(self, host_id: int) -> dict[str, Any]:
        return self.platform.host_acceptance_report(host_id)

    def list_platform_backups(self) -> list[dict[str, Any]]:
        return self.platform.list_backups()

    def list_platform_snapshots(self) -> list[dict[str, Any]]:
        return self.platform.list_snapshots()

    def list_platform_tasks(self) -> list[dict[str, Any]]:
        return self.platform.list_tasks()

    def list_platform_audit(self) -> list[dict[str, Any]]:
        return self.platform.list_audit_events()


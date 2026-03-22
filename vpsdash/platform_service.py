from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .capacity import can_allocate, capacity_summary, gpu_assignment_preflight, summarize_inventory
from .config import PlatformConfig, load_config
from .database import apply_schema_migrations, create_platform_engine, create_session_factory, session_scope
from .execution import PlanCancelled, describe_doplet_terminal, inspect_doplet_runtime, inspect_host_domains, open_doplet_terminal
from .host_agent import HostAgent
from .mailer import Mailer
from .models import (
    AuditEvent,
    BackupProvider,
    BackupRecord,
    Base,
    Flavor,
    Doplet,
    HostNode,
    ImageRecord,
    LoginChallenge,
    Network,
    SnapshotRecord,
    TaskRecord,
    TrustedDevice,
    UserAccount,
)
from .orchestration import backup_plan, clone_plan, doplet_create_plan, doplet_lifecycle_plan, host_prepare_plan, restore_plan, snapshot_plan
from .orchestration import network_apply_plan, network_delete_plan, resize_plan
from .security import (
    decrypt_secret,
    encrypt_secret,
    expires_in,
    hash_password,
    hash_verification_code,
    make_device_fingerprint,
    make_trust_token,
    make_verification_code,
    sanitize_device_payload,
    sign_json_payload,
    utc_now as security_now,
    verify_code,
    verify_password,
    verify_trust_token,
)


def _slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "item"


def _unique_slug(session: Session, model: Any, candidate: str, *, existing_id: int | None = None) -> str:
    base = _slugify(candidate)
    slug = base
    suffix = 2
    while True:
        existing = session.scalar(select(model.id).where(model.slug == slug))
        if existing is None or (existing_id is not None and int(existing) == int(existing_id)):
            return slug
        slug = f"{base}-{suffix}"
        suffix += 1


def _coerce_utc(value: Any) -> Any:
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=security_now().tzinfo)
    return value


def _make_bootstrap_password() -> str:
    return "bypass"


def _doplet_is_deleted(value: Any) -> bool:
    if isinstance(value, dict):
        status = value.get("status")
    else:
        status = getattr(value, "status", None)
    return str(status or "").strip().lower() == "deleted"


def _host_runtime_key(payload: dict[str, Any]) -> str:
    mode = str(payload.get("host_mode") or payload.get("mode") or "").strip().lower()
    distro = str(payload.get("wsl_distribution") or payload.get("distro") or "").strip().lower()
    if mode in {"windows-local", "windows-wsl-local", "linux-local", "linux-hypervisor"}:
        return f"{mode}|{distro}"
    ssh_host = str(payload.get("ssh_host") or "").strip().lower()
    ssh_user = str(payload.get("ssh_user") or "").strip().lower()
    ssh_port = str(payload.get("ssh_port") or "").strip().lower()
    return f"{mode}|{ssh_user}|{ssh_host}|{ssh_port}|{distro}"


def _host_runtime_priority(payload: dict[str, Any], live_doplet_count: int) -> tuple[int, int]:
    status = str(payload.get("status") or "").strip().lower()
    ready_rank = 2 if status == "ready" else 1 if status in {"provisioning", "queued"} else 0
    return (int(live_doplet_count), ready_rank)


def _suffixed_slug_base(slug: str) -> str:
    value = str(slug or "").strip()
    if "-" not in value:
        return ""
    base, suffix = value.rsplit("-", 1)
    return base if suffix.isdigit() else ""


@dataclass(slots=True)
class LoginResult:
    ok: bool
    user: dict[str, Any] | None = None
    challenge_required: bool = False
    challenge_id: int | None = None
    trusted_device_token: str | None = None
    message: str = ""


class PlatformService:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.config: PlatformConfig = load_config(root)
        self.engine = create_platform_engine(self.config)
        apply_schema_migrations(self.engine)
        self.session_factory = create_session_factory(self.engine)
        Base.metadata.create_all(self.engine)
        self.mailer = Mailer(self.config)
        self.agent = HostAgent(self.config)
        self._task_threads: dict[int, threading.Thread] = {}
        self._task_cancel_events: dict[int, threading.Event] = {}
        self._task_lock = threading.Lock()
        self._last_runtime_reconcile_at = 0.0
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        with session_scope(self.session_factory) as session:
            if not session.scalar(select(UserAccount.id).limit(1)):
                owner = UserAccount(
                    username="owner",
                    email="owner@vpsdash.local",
                    password_hash=hash_password("change-me-now"),
                    role="owner",
                    mfa_enabled=bool(self.config.email_login_verification_enabled),
                    mfa_method="email",
                )
                session.add(owner)
                session.add(
                    AuditEvent(
                        actor="system",
                        action="seed.owner",
                        target_type="user",
                        target_id="owner",
                        summary="Created default owner account",
                        details={"username": "owner"},
                    )
                )
            if not session.scalar(select(ImageRecord.id).limit(1)):
                session.add_all(
                    [
                        ImageRecord(
                            slug="ubuntu-24-04-lts",
                            name="Ubuntu Server 24.04 LTS",
                            distro="ubuntu",
                            version="24.04",
                            source_url="https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-amd64.img",
                        ),
                        ImageRecord(
                            slug="debian-12",
                            name="Debian 12",
                            distro="debian",
                            version="12",
                            source_url="https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2",
                        ),
                        ImageRecord(
                            slug="fedora-41",
                            name="Fedora Cloud 41",
                            distro="fedora",
                            version="41",
                            source_url="https://download.fedoraproject.org/pub/fedora/linux/releases/41/Cloud/x86_64/images/",
                        ),
                        ImageRecord(
                            slug="alpine-3-21",
                            name="Alpine 3.21",
                            distro="alpine",
                            version="3.21",
                            source_url="https://dl-cdn.alpinelinux.org/alpine/v3.21/releases/cloud/",
                        ),
                    ]
                )
            if not session.scalar(select(Flavor.id).limit(1)):
                session.add_all(
                    [
                        Flavor(slug="micro", name="Micro", vcpu=1, ram_mb=1024, disk_gb=20, gpu_mode="none"),
                        Flavor(slug="starter", name="Starter", vcpu=2, ram_mb=2048, disk_gb=40, gpu_mode="none"),
                        Flavor(slug="builder", name="Builder", vcpu=4, ram_mb=8192, disk_gb=80, gpu_mode="passthrough-ready"),
                    ]
                )

    def _audit(self, session: Session, *, actor: str, action: str, target_type: str, target_id: str, summary: str, details: dict[str, Any] | None = None) -> None:
        session.add(
            AuditEvent(
                actor=actor,
                action=action,
                target_type=target_type,
                target_id=str(target_id),
                summary=summary,
                details=details or {},
            )
        )

    def bootstrap(self) -> dict[str, Any]:
        self.reconcile_runtime_state()
        with session_scope(self.session_factory) as session:
            doplet_records = session.scalars(select(Doplet).order_by(Doplet.name)).all()
            doplet_payloads = [self._doplet_payload(item) for item in doplet_records]
            live_doplet_payloads = [item for item in doplet_payloads if not _doplet_is_deleted(item)]
            archived_doplet_payloads = [item for item in doplet_payloads if _doplet_is_deleted(item)]
            host_payloads = [self._host_payload_with_capacity(session, item, live_doplet_payloads) for item in session.scalars(select(HostNode).order_by(HostNode.name)).all()]
            return {
                "counts": {
                    "users": session.query(UserAccount).count(),
                    "hosts": session.query(HostNode).count(),
                    "doplets": len(live_doplet_payloads),
                    "archived_doplets": len(archived_doplet_payloads),
                    "snapshots": session.query(SnapshotRecord).count(),
                    "networks": session.query(Network).count(),
                    "providers": session.query(BackupProvider).count(),
                    "tasks": session.query(TaskRecord).count(),
                },
                "config": {
                    "database_url": self.config.database_url,
                    "public_base_url": self.config.public_base_url,
                    "lan_only_default": self.config.lan_only_default,
                    "default_host_distro": self.config.default_host_distro,
                },
                "images": [self._image_payload(item) for item in session.scalars(select(ImageRecord).order_by(ImageRecord.name)).all()],
                "flavors": [self._flavor_payload(item) for item in session.scalars(select(Flavor).order_by(Flavor.name)).all()],
                "hosts": host_payloads,
                "doplets": live_doplet_payloads,
                "archived_doplets": archived_doplet_payloads,
                "snapshots": [self._snapshot_payload(item) for item in session.scalars(select(SnapshotRecord).order_by(SnapshotRecord.created_at.desc()).limit(50)).all()],
                "networks": [self._network_payload(item) for item in session.scalars(select(Network).order_by(Network.name)).all()],
                "providers": [self._provider_payload(item) for item in session.scalars(select(BackupProvider).order_by(BackupProvider.name)).all()],
                "tasks": [self._task_payload(item) for item in session.scalars(select(TaskRecord).order_by(TaskRecord.created_at.desc()).limit(25)).all()],
                "audit": [self._audit_payload(item) for item in session.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(25)).all()],
            }

    def reconcile_runtime_state(self, *, actor: str = "system", force: bool = False) -> dict[str, Any]:
        now_monotonic = time.monotonic()
        if not force and (now_monotonic - self._last_runtime_reconcile_at) < 3.0:
            return {"changed": 0, "checked": 0}
        changed = 0
        checked = 0
        imported = 0
        revived = 0
        with session_scope(self.session_factory) as session:
            active_doplet_ids = {
                int(task.target_id)
                for task in session.scalars(
                    select(TaskRecord).where(
                        TaskRecord.target_type == "doplet",
                        TaskRecord.status.in_(["planned", "queued", "running", "cancel-requested"]),
                    )
                ).all()
                if str(task.target_id or "").isdigit()
            }
            hosts = session.scalars(select(HostNode).order_by(HostNode.id)).all()
            all_doplets = session.scalars(select(Doplet).order_by(Doplet.id)).all()
            host_payload_by_id = {int(host.id): self._host_payload(host) for host in hosts}
            runtime_key_by_host_id = {host_id: _host_runtime_key(payload) for host_id, payload in host_payload_by_id.items()}
            live_doplet_count_by_host_id = {
                int(host.id): sum(1 for item in all_doplets if int(item.host_id or 0) == int(host.id) and not _doplet_is_deleted(item))
                for host in hosts
            }
            ordered_hosts = sorted(
                hosts,
                key=lambda item: (
                    _host_runtime_priority(host_payload_by_id[int(item.id)], live_doplet_count_by_host_id.get(int(item.id), 0)),
                    -int(item.id),
                ),
                reverse=True,
            )
            scanned_runtime_keys: set[str] = set()
            for host in ordered_hosts:
                host_id = int(host.id)
                host_payload = host_payload_by_id[host_id]
                runtime_key = runtime_key_by_host_id.get(host_id, "")
                if runtime_key in scanned_runtime_keys:
                    inventory = dict(host.inventory or {})
                    inventory["runtime_scan_skipped_duplicate_of"] = runtime_key
                    inventory["runtime_scan_skipped_at"] = self._now().isoformat()
                    host.inventory = inventory
                    continue
                scanned_runtime_keys.add(runtime_key)
                doplets = [
                    item
                    for item in all_doplets
                    if runtime_key_by_host_id.get(int(item.host_id or 0)) == runtime_key
                ]
                domains: list[dict[str, Any]] = []
                try:
                    domains = inspect_host_domains(host_payload)
                except Exception as exc:
                    inventory = dict(host.inventory or {})
                    inventory["runtime_domain_scan_error"] = str(exc)
                    inventory["runtime_domain_scan_error_at"] = self._now().isoformat()
                    host.inventory = inventory
                    domains = []
                domain_map = {str(item.get("slug") or "").strip(): item for item in domains if str(item.get("slug") or "").strip()}
                existing_by_slug = {str(item.slug or "").strip(): item for item in doplets if str(item.slug or "").strip()}
                for domain in domains:
                    slug = str(domain.get("slug") or "").strip()
                    if not slug:
                        continue
                    existing = existing_by_slug.get(slug)
                    if existing is None:
                        imported_doplet = Doplet(
                            slug=_unique_slug(session, Doplet, slug),
                            name=str(domain.get("name") or slug),
                            host_id=host.id,
                            status=str(domain.get("status") or "running"),
                            ip_addresses=[str(item).strip() for item in domain.get("ip_addresses") or [] if str(item).strip()],
                            storage_backend=host.primary_storage_backend or "files",
                            bootstrap_user="ubuntu",
                            metadata_json={
                                "orphan_imported": True,
                                "orphan_imported_at": self._now().isoformat(),
                                "runtime_raw_state": str(domain.get("raw_state") or ""),
                                "runtime_last_seen_at": self._now().isoformat(),
                                "runtime_last_ip_addresses": [str(item).strip() for item in domain.get("ip_addresses") or [] if str(item).strip()],
                            },
                        )
                        session.add(imported_doplet)
                        session.flush()
                        existing_by_slug[slug] = imported_doplet
                        doplets.append(imported_doplet)
                        changed += 1
                        imported += 1
                        continue
                    if _doplet_is_deleted(existing):
                        metadata = dict(existing.metadata_json or {})
                        metadata["deleted_at"] = ""
                        metadata["revived_from_runtime_reconcile_at"] = self._now().isoformat()
                        metadata["runtime_raw_state"] = str(domain.get("raw_state") or "")
                        metadata["runtime_last_seen_at"] = self._now().isoformat()
                        metadata["runtime_last_ip_addresses"] = [str(item).strip() for item in domain.get("ip_addresses") or [] if str(item).strip()]
                        existing.status = str(domain.get("status") or "running")
                        existing.ip_addresses = [str(item).strip() for item in domain.get("ip_addresses") or [] if str(item).strip()]
                        existing.metadata_json = metadata
                        changed += 1
                        revived += 1
                for doplet in doplets:
                    metadata = dict(doplet.metadata_json or {})
                    base_slug = _suffixed_slug_base(str(doplet.slug or ""))
                    if not base_slug or base_slug not in domain_map:
                        continue
                    if not metadata.get("orphan_imported"):
                        continue
                    if str(doplet.status or "").strip().lower() != "missing":
                        continue
                    metadata["deleted_at"] = metadata.get("deleted_at") or self._now().isoformat()
                    metadata["superseded_by_runtime_slug"] = base_slug
                    metadata["superseded_at"] = self._now().isoformat()
                    doplet.status = "deleted"
                    doplet.ip_addresses = []
                    doplet.metadata_json = metadata
                    changed += 1
                for doplet in doplets:
                    if _doplet_is_deleted(doplet) or doplet.id in active_doplet_ids:
                        continue
                    checked += 1
                    domain_runtime = domain_map.get(str(doplet.slug or "").strip())
                    if domain_runtime is not None:
                        runtime = {
                            "exists": True,
                            "raw_state": str(domain_runtime.get("raw_state") or ""),
                            "status": str(domain_runtime.get("status") or doplet.status or "").strip().lower() or doplet.status,
                            "ip_addresses": [str(item).strip() for item in domain_runtime.get("ip_addresses") or [] if str(item).strip()],
                        }
                    else:
                        try:
                            runtime = inspect_doplet_runtime(host_payload, self._doplet_payload(doplet))
                        except Exception as exc:
                            inventory = dict(host.inventory or {})
                            inventory["runtime_sync_error"] = str(exc)
                            inventory["runtime_sync_error_at"] = self._now().isoformat()
                            host.inventory = inventory
                            continue
                    next_status = str(runtime.get("status") or doplet.status or "").strip().lower() or doplet.status
                    next_ips = [str(item).strip() for item in runtime.get("ip_addresses") or [] if str(item).strip()]
                    doplet_changed = False
                    if next_status and next_status != str(doplet.status or "").strip().lower():
                        doplet.status = next_status
                        doplet_changed = True
                    if next_status in {"stopped", "paused", "error", "missing"}:
                        if doplet.ip_addresses:
                            doplet.ip_addresses = []
                            doplet_changed = True
                    elif next_ips and next_ips != list(doplet.ip_addresses or []):
                        doplet.ip_addresses = next_ips
                        doplet_changed = True
                    metadata = dict(doplet.metadata_json or {})
                    metadata["runtime_last_seen_at"] = self._now().isoformat()
                    metadata["runtime_raw_state"] = str(runtime.get("raw_state") or "")
                    if next_ips:
                        metadata["runtime_last_ip_addresses"] = next_ips
                    elif next_status in {"stopped", "paused", "error", "missing"}:
                        metadata["runtime_last_ip_addresses"] = []
                    if metadata != dict(doplet.metadata_json or {}):
                        doplet.metadata_json = metadata
                        doplet_changed = True
                    host_mode = str(host_payload.get("host_mode") or host_payload.get("mode") or "").strip().lower()
                    if next_status == "running" and host_mode in {"windows-local", "windows-wsl-local"}:
                        try:
                            terminal_details = describe_doplet_terminal(
                                host_payload,
                                self._doplet_payload(doplet),
                                establish_localhost_endpoint=True,
                            )
                            self._persist_doplet_terminal_details(doplet, terminal_details)
                            doplet_changed = True
                        except Exception as exc:
                            metadata = dict(doplet.metadata_json or {})
                            metadata["access_refresh_error"] = str(exc)
                            metadata["access_refresh_error_at"] = self._now().isoformat()
                            if metadata != dict(doplet.metadata_json or {}):
                                doplet.metadata_json = metadata
                                doplet_changed = True
                    if doplet_changed:
                        changed += 1
            if changed:
                session.add(
                    AuditEvent(
                        actor=actor,
                        action="runtime.reconcile",
                        target_type="control-plane",
                        target_id="doplets",
                        summary=f"Reconciled {changed} Doplet runtime records",
                        details={"checked": checked, "changed": changed, "imported": imported, "revived": revived},
                    )
                )
        self._last_runtime_reconcile_at = time.monotonic()
        return {"changed": changed, "checked": checked, "imported": imported, "revived": revived}

    def list_users(self) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            return [self._user_payload(item) for item in session.scalars(select(UserAccount).order_by(UserAccount.username)).all()]

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with session_scope(self.session_factory) as session:
            user = session.get(UserAccount, user_id)
            return self._user_payload(user) if user else None

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with session_scope(self.session_factory) as session:
            user = session.scalar(select(UserAccount).where(UserAccount.username == username))
            return self._user_payload(user) if user else None

    def list_backups(self) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            return [self._backup_payload(item) for item in session.scalars(select(BackupRecord).order_by(BackupRecord.created_at.desc())).all()]

    def list_snapshots(self) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            return [self._snapshot_payload(item) for item in session.scalars(select(SnapshotRecord).order_by(SnapshotRecord.created_at.desc())).all()]

    def list_tasks(self) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            return [self._task_payload(item) for item in session.scalars(select(TaskRecord).order_by(TaskRecord.created_at.desc())).all()]

    def list_audit_events(self) -> list[dict[str, Any]]:
        with session_scope(self.session_factory) as session:
            return [self._audit_payload(item) for item in session.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc())).all()]

    def _backup_policy(self, doplet: Doplet | dict[str, Any] | None) -> dict[str, Any]:
        if doplet is None:
            return {}
        metadata = doplet.get("metadata_json") if isinstance(doplet, dict) else doplet.metadata_json
        policy = dict((metadata or {}).get("backup_policy") or {})
        policy["enabled"] = bool(policy.get("enabled", False))
        policy["schedule_minutes"] = int(policy.get("schedule_minutes") or 0)
        policy["retain_count"] = max(0, int(policy.get("retain_count") or 0))
        policy["provider_ids"] = [int(item) for item in policy.get("provider_ids") or [] if str(item).strip()]
        policy["verify_after_upload"] = bool(policy.get("verify_after_upload", True))
        policy["prune_remote"] = bool(policy.get("prune_remote", False))
        policy["last_scheduled_at"] = policy.get("last_scheduled_at")
        return policy

    def _set_backup_policy(self, doplet: Doplet, policy: dict[str, Any]) -> None:
        metadata = dict(doplet.metadata_json or {})
        metadata["backup_policy"] = policy
        doplet.metadata_json = metadata

    def _now(self) -> datetime:
        return datetime.now(timezone.utc).replace(microsecond=0)

    def _remember_trusted_device(
        self,
        session: Session,
        *,
        user: UserAccount,
        fingerprint: str,
        device_name: str,
    ) -> str:
        trust_token, trust_hash = make_trust_token()
        trusted_device = session.scalar(
            select(TrustedDevice).where(
                TrustedDevice.user_id == user.id,
                TrustedDevice.device_fingerprint == fingerprint,
            )
        )
        if trusted_device is None:
            try:
                with session.begin_nested():
                    session.add(
                        TrustedDevice(
                            user_id=user.id,
                            device_fingerprint=fingerprint,
                            device_name=device_name,
                            trust_token_hash=trust_hash,
                            last_seen_at=security_now(),
                        )
                    )
                    session.flush()
            except IntegrityError:
                trusted_device = session.scalar(
                    select(TrustedDevice).where(
                        TrustedDevice.user_id == user.id,
                        TrustedDevice.device_fingerprint == fingerprint,
                    )
                )
            else:
                return trust_token

        if trusted_device is not None:
            trusted_device.trust_token_hash = trust_hash
            trusted_device.device_name = device_name
            trusted_device.last_seen_at = security_now()
        return trust_token

    def create_user(self, payload: dict[str, Any], *, actor: str = "system") -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            username = payload["username"].strip()
            email = payload["email"].strip().lower()
            user = UserAccount(
                username=username,
                email=email,
                password_hash=hash_password(payload["password"]),
                role=payload.get("role", "operator"),
                mfa_enabled=bool(payload.get("mfa_enabled", True)),
                mfa_method=payload.get("mfa_method", "email"),
                status="active",
            )
            session.add(user)
            session.flush()
            self._audit(session, actor=actor, action="user.create", target_type="user", target_id=str(user.id), summary=f"Created user {username}")
            return self._user_payload(user)

    def authenticate_user(self, username_or_email: str, password: str, device_payload: dict[str, Any], *, actor: str = "web") -> LoginResult:
        device = sanitize_device_payload(device_payload)
        fingerprint = make_device_fingerprint(device["fingerprint_source"])
        with session_scope(self.session_factory) as session:
            user = session.scalar(
                select(UserAccount).where(
                    (UserAccount.username == username_or_email) | (UserAccount.email == username_or_email.lower())
                )
            )
            if not user or not verify_password(user.password_hash, password):
                return LoginResult(ok=False, message="Invalid credentials.")

            trusted_device = session.scalar(
                select(TrustedDevice).where(
                    TrustedDevice.user_id == user.id,
                    TrustedDevice.device_fingerprint == fingerprint,
                )
            )
            if self.config.email_login_verification_enabled and user.mfa_enabled and not trusted_device:
                code = make_verification_code()
                challenge = LoginChallenge(
                    user_id=user.id,
                    challenge_type="email-mfa",
                    device_fingerprint=fingerprint,
                    verification_code_hash=hash_verification_code(code),
                    expires_at=expires_in(self.config.login_mfa_ttl_minutes),
                )
                session.add(challenge)
                session.flush()
                self.mailer.send(
                    to_address=user.email,
                    subject="VPSdash login verification",
                    body=(
                        f"Your VPSdash verification code is {code}.\n\n"
                        f"Device: {device['device_name']}\n"
                        f"IP: {device['ip_address'] or 'unknown'}\n"
                    ),
                    metadata={"challenge_id": challenge.id, "username": user.username},
                )
                self.mailer.send(
                    to_address=user.email,
                    subject="VPSdash new device alert",
                    body=(
                        f"A new device is attempting to sign in to VPSdash.\n\n"
                        f"Device: {device['device_name']}\n"
                        f"IP: {device['ip_address'] or 'unknown'}\n"
                        f"If this was you, enter the verification code sent separately."
                    ),
                    metadata={"challenge_id": challenge.id, "alert_type": "new-device"},
                )
                self._audit(
                    session,
                    actor=actor,
                    action="auth.challenge_issued",
                    target_type="user",
                    target_id=str(user.id),
                    summary=f"Issued MFA challenge for {user.username}",
                    details={"device_name": device["device_name"], "fingerprint": fingerprint},
                )
                return LoginResult(ok=False, challenge_required=True, challenge_id=challenge.id, message="Verification required.")

            trust_token = self._remember_trusted_device(
                session,
                user=user,
                fingerprint=fingerprint,
                device_name=device["device_name"],
            )
            user.last_login_at = security_now()
            self._audit(session, actor=actor, action="auth.login", target_type="user", target_id=str(user.id), summary=f"Authenticated {user.username}")
            return LoginResult(ok=True, user=self._user_payload(user), trusted_device_token=trust_token)

    def verify_login_challenge(self, challenge_id: int, code: str, device_payload: dict[str, Any], *, actor: str = "web") -> LoginResult:
        device = sanitize_device_payload(device_payload)
        fingerprint = make_device_fingerprint(device["fingerprint_source"])
        with session_scope(self.session_factory) as session:
            challenge = session.get(LoginChallenge, challenge_id)
            if not challenge or challenge.consumed_at is not None or _coerce_utc(challenge.expires_at) < security_now():
                return LoginResult(ok=False, message="Challenge expired or invalid.")
            if challenge.device_fingerprint != fingerprint:
                return LoginResult(ok=False, message="Device fingerprint mismatch.")
            if not verify_code(challenge.verification_code_hash, code):
                return LoginResult(ok=False, message="Invalid verification code.")
            user = session.get(UserAccount, challenge.user_id)
            trust_token = self._remember_trusted_device(
                session,
                user=user,
                fingerprint=fingerprint,
                device_name=device["device_name"],
            )
            challenge.consumed_at = security_now()
            user.last_login_at = security_now()
            self._audit(session, actor=actor, action="auth.challenge_verified", target_type="user", target_id=str(user.id), summary=f"Verified MFA for {user.username}")
            return LoginResult(ok=True, user=self._user_payload(user), trusted_device_token=trust_token)

    def verify_trusted_device(self, user_id: int, fingerprint_source: str, trust_token: str) -> bool:
        fingerprint = make_device_fingerprint(fingerprint_source)
        with session_scope(self.session_factory) as session:
            trusted_device = session.scalar(
                select(TrustedDevice).where(TrustedDevice.user_id == user_id, TrustedDevice.device_fingerprint == fingerprint)
            )
            if not trusted_device:
                return False
            return verify_trust_token(trusted_device.trust_token_hash, trust_token)

    def upsert_host(self, payload: dict[str, Any], *, actor: str = "system") -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            host = None
            host_id = payload.get("id")
            requested_slug = _slugify(payload.get("slug") or payload.get("name") or "host")
            if host_id:
                host = session.get(HostNode, int(host_id))
            if host is None:
                host = session.scalar(select(HostNode).where(HostNode.slug == requested_slug))
            if host is None:
                host = HostNode(slug=_unique_slug(session, HostNode, requested_slug), name=payload.get("name") or "New host")
                session.add(host)
            host.name = payload.get("name") or host.name
            host.slug = _unique_slug(session, HostNode, requested_slug, existing_id=host.id)
            host.host_mode = payload.get("host_mode") or payload.get("mode") or host.host_mode
            host.distro = payload.get("distro") or self.config.default_host_distro
            host.exposure_mode = payload.get("exposure_mode") or ("lan-vpn-only" if self.config.lan_only_default else "public-opt-in")
            host.primary_storage_backend = payload.get("primary_storage_backend") or payload.get("storage_backend") or host.primary_storage_backend or "files"
            host.ssh_host = payload.get("ssh_host") or host.ssh_host
            host.ssh_user = payload.get("ssh_user") or host.ssh_user
            host.ssh_port = int(payload.get("ssh_port") or host.ssh_port or 22)
            host.wsl_distribution = payload.get("wsl_distribution") or host.wsl_distribution
            host.mixed_use_allowed = bool(payload.get("mixed_use_allowed", host.mixed_use_allowed))
            host.mixed_use_warning_acknowledged = bool(payload.get("mixed_use_warning_acknowledged", host.mixed_use_warning_acknowledged))
            host.status = payload.get("status") or host.status
            inventory = dict(host.inventory or {})
            if payload.get("inventory"):
                inventory.update(payload.get("inventory") or {})
            config = dict(inventory.get("config") or {})
            if "config" in payload:
                config.update(payload.get("config") or {})
            for source_key, config_key in (
                ("agent_endpoint", "agent_endpoint"),
                ("agent_secret", "agent_secret"),
                ("agent_mode", "agent_mode"),
                ("reserve_cpu_threads", "reserve_cpu_threads"),
                ("reserve_ram_mb", "reserve_ram_mb"),
                ("reserve_disk_gb", "reserve_disk_gb"),
                ("runtime_root", "runtime_root"),
                ("zfs_pool", "zfs_pool"),
                ("zfs_dataset_root", "zfs_dataset_root"),
                ("lvm_vg", "lvm_vg"),
                ("lvm_thinpool", "lvm_thinpool"),
                ("libvirt_network", "libvirt_network"),
            ):
                if payload.get(source_key) not in {None, ""}:
                    config[config_key] = payload.get(source_key)
            inventory["config"] = config
            host.inventory = inventory
            host.warnings = payload.get("warnings") or host.warnings or []
            host.notes = payload.get("notes") or host.notes
            session.flush()
            self._audit(session, actor=actor, action="host.upsert", target_type="host", target_id=str(host.id), summary=f"Upserted host {host.name}")
            return self._host_payload(host)

    def capture_host_inventory(self, host_id: int, *, actor: str = "system") -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            host = session.get(HostNode, host_id)
            if not host:
                raise KeyError(f"Unknown host: {host_id}")
            host_payload = self._host_payload(host)
            snapshot = self.agent.capture_inventory(host_payload)
            inventory = dict(host.inventory or {})
            inventory["snapshot"] = snapshot
            inventory["resources"] = summarize_inventory(snapshot)
            inventory["captured_at"] = security_now().isoformat()
            host.inventory = inventory
            warnings: list[str] = []
            if host.mixed_use_allowed and not host.mixed_use_warning_acknowledged:
                warnings.append("Mixed-use mode is enabled but warnings are not yet acknowledged.")
            resources = inventory.get("resources") or {}
            if not resources.get("virtualization_ready"):
                warnings.append("Libvirt did not validate cleanly during inventory capture.")
            if resources.get("iommu_groups", 0) <= 0:
                warnings.append("IOMMU groups were not detected. Passthrough and mediated devices may be unavailable.")
            if host_payload.get("host_mode") in {"windows-local", "windows-remote", "windows-wsl-remote"}:
                wsl_probe = snapshot.get("wsl_list") or {}
                if not wsl_probe.get("ok"):
                    warnings.append("The Windows host could not confirm the selected WSL runtime during inventory capture.")
            host.warnings = list(dict.fromkeys(warnings))
            session.flush()
            self._audit(session, actor=actor, action="host.inventory_capture", target_type="host", target_id=str(host.id), summary=f"Captured inventory for {host.name}")
            doplets = [self._doplet_payload(item) for item in session.scalars(select(Doplet).where(Doplet.host_id == host.id)).all()]
            return self._host_payload_with_capacity(session, host, doplets)

    def queue_prepare_host(self, host_id: int, *, actor: str = "system") -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            host = session.get(HostNode, host_id)
            if not host:
                raise KeyError(f"Unknown host: {host_id}")
            command_plan = host_prepare_plan(self._host_payload(host))
            task = TaskRecord(
                task_type="prepare-host",
                target_type="host",
                target_id=str(host.id),
                status="planned",
                command_plan=command_plan,
                result_payload=self._task_security_payload("prepare-host", "host", str(host.id), command_plan),
            )
            session.add(task)
            session.flush()
            self._audit(session, actor=actor, action="task.queue", target_type="task", target_id=str(task.id), summary=f"Queued host preparation for {host.name}")
            return self._task_payload(task)

    def upsert_network(self, payload: dict[str, Any], *, actor: str = "system") -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            network = session.get(Network, int(payload["id"])) if payload.get("id") else None
            if network is None:
                network = Network(host_id=int(payload["host_id"]), slug=_slugify(payload.get("slug") or payload.get("name") or "network"), name=payload.get("name") or "New network")
                session.add(network)
            network.name = payload.get("name") or network.name
            network.mode = payload.get("mode") or network.mode
            network.cidr = payload.get("cidr") or network.cidr
            network.bridge_name = payload.get("bridge_name") or network.bridge_name
            network.nat_enabled = bool(payload.get("nat_enabled", network.nat_enabled))
            network.exposure_mode = payload.get("exposure_mode") or network.exposure_mode
            network.firewall_policy = payload.get("firewall_policy") or network.firewall_policy or {}
            session.flush()
            self._audit(session, actor=actor, action="network.upsert", target_type="network", target_id=str(network.id), summary=f"Upserted network {network.name}")
            return self._network_payload(network)

    def queue_apply_network(self, network_id: int, *, actor: str = "system") -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            network = session.get(Network, network_id)
            if not network:
                raise KeyError(f"Unknown network: {network_id}")
            host = session.get(HostNode, network.host_id)
            command_plan = network_apply_plan(self._host_payload(host), self._network_payload(network))
            task = TaskRecord(
                task_type="apply-network",
                target_type="network",
                target_id=str(network.id),
                status="planned",
                command_plan=command_plan,
                result_payload=self._task_security_payload("apply-network", "network", str(network.id), command_plan),
            )
            session.add(task)
            session.flush()
            self._audit(session, actor=actor, action="task.queue", target_type="task", target_id=str(task.id), summary=f"Queued network apply for {network.name}")
            return self._task_payload(task)

    def queue_delete_network_runtime(self, network_id: int, *, actor: str = "system") -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            network = session.get(Network, network_id)
            if not network:
                raise KeyError(f"Unknown network: {network_id}")
            host = session.get(HostNode, network.host_id)
            command_plan = network_delete_plan(self._host_payload(host), self._network_payload(network))
            task = TaskRecord(
                task_type="delete-network-runtime",
                target_type="network",
                target_id=str(network.id),
                status="planned",
                command_plan=command_plan,
                result_payload=self._task_security_payload("delete-network-runtime", "network", str(network.id), command_plan),
            )
            session.add(task)
            session.flush()
            self._audit(session, actor=actor, action="task.queue", target_type="task", target_id=str(task.id), summary=f"Queued network runtime delete for {network.name}")
            return self._task_payload(task)

    def upsert_backup_provider(self, payload: dict[str, Any], *, actor: str = "system") -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            provider = session.get(BackupProvider, int(payload["id"])) if payload.get("id") else None
            if provider is None:
                provider = BackupProvider(slug=_slugify(payload.get("slug") or payload.get("name") or "provider"), name=payload.get("name") or "Backup Provider")
                session.add(provider)
            provider.name = payload.get("name") or provider.name
            provider.provider_type = payload.get("provider_type") or provider.provider_type
            provider.endpoint = payload.get("endpoint") or provider.endpoint
            provider.bucket = payload.get("bucket") or provider.bucket
            provider.region = payload.get("region") or provider.region
            provider.root_path = payload.get("root_path") or provider.root_path
            provider.access_key_id = payload.get("access_key_id") or provider.access_key_id
            if "secret_key" in payload:
                provider.secret_key_encrypted = encrypt_secret(self.config, payload.get("secret_key", ""))
            provider.quota_model = payload.get("quota_model") or provider.quota_model or {}
            provider.enabled = bool(payload.get("enabled", provider.enabled))
            provider.policy_notes = payload.get("policy_notes") or provider.policy_notes
            session.flush()
            self._audit(session, actor=actor, action="provider.upsert", target_type="provider", target_id=str(provider.id), summary=f"Upserted provider {provider.name}")
            return self._provider_payload(provider)

    def test_backup_provider(self, provider_id: int) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            provider = session.get(BackupProvider, provider_id)
            if not provider:
                raise KeyError(f"Unknown provider: {provider_id}")
            if provider.provider_type == "local":
                path = Path(provider.root_path or self.config.data_dir / "provider-backups")
                path.mkdir(parents=True, exist_ok=True)
                return {"ok": True, "provider": self._provider_payload(provider), "detail": f"Local path ready at {path}"}
            client = boto3.client(
                "s3",
                endpoint_url=provider.endpoint or None,
                region_name=provider.region or None,
                aws_access_key_id=provider.access_key_id or None,
                aws_secret_access_key=decrypt_secret(self.config, provider.secret_key_encrypted) or None,
            )
            client.list_buckets()
            return {"ok": True, "provider": self._provider_payload(provider), "detail": "S3-compatible provider reachable"}

    def upsert_doplet(self, payload: dict[str, Any], *, actor: str = "system") -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            doplet = session.get(Doplet, int(payload["id"])) if payload.get("id") else None
            flavor = session.get(Flavor, int(payload["flavor_id"])) if payload.get("flavor_id") else None
            resolved_name = str(payload.get("name") or "").strip() or "New Doplet"
            requested_slug = str(payload.get("slug") or "").strip() or resolved_name or "doplet"
            if doplet is None:
                doplet = Doplet(
                    slug=_unique_slug(session, Doplet, requested_slug),
                    name=resolved_name,
                    host_id=int(payload["host_id"]),
                )
                session.add(doplet)
            requested_host_id = int(payload.get("host_id") or doplet.host_id)
            host = session.get(HostNode, requested_host_id)
            if not host:
                raise KeyError(f"Unknown host: {requested_host_id}")

            requested_vcpu = int(payload.get("vcpu") or (flavor.vcpu if flavor else doplet.vcpu or 1))
            requested_ram_mb = int(payload.get("ram_mb") or (flavor.ram_mb if flavor else doplet.ram_mb or 1024))
            requested_disk_gb = int(payload.get("disk_gb") or (flavor.disk_gb if flavor else doplet.disk_gb or 20))
            requested_gpu_assignments = payload.get("gpu_assignments") or doplet.gpu_assignments or []

            peer_records = session.scalars(select(Doplet).where(Doplet.host_id == requested_host_id)).all()
            peer_payloads = [
                self._doplet_payload(item)
                for item in peer_records
                if doplet.id is None or item.id != doplet.id
            ]
            host_payload = self._host_payload(host)
            capacity = capacity_summary(host_payload, peer_payloads)
            if any(capacity.get("totals", {}).values()):
                okay, errors = can_allocate(
                    capacity,
                    {
                        "vcpu": requested_vcpu,
                        "ram_mb": requested_ram_mb,
                        "disk_gb": requested_disk_gb,
                        "gpu_assignments": requested_gpu_assignments,
                    },
                )
                if not okay:
                    raise ValueError(" ".join(errors))
            gpu_preflight = gpu_assignment_preflight(host_payload, requested_gpu_assignments)
            if not gpu_preflight["ok"]:
                raise ValueError(" ".join(gpu_preflight["errors"]))

            doplet.name = resolved_name
            doplet.slug = _unique_slug(session, Doplet, requested_slug, existing_id=doplet.id)
            doplet.host_id = requested_host_id
            doplet.image_id = int(payload["image_id"]) if payload.get("image_id") else doplet.image_id
            doplet.flavor_id = int(payload["flavor_id"]) if payload.get("flavor_id") else doplet.flavor_id
            doplet.status = payload.get("status") or doplet.status
            doplet.vcpu = requested_vcpu
            doplet.ram_mb = requested_ram_mb
            doplet.disk_gb = requested_disk_gb
            doplet.primary_network_id = int(payload["primary_network_id"]) if payload.get("primary_network_id") else doplet.primary_network_id
            doplet.network_ids = [int(item) for item in payload.get("network_ids") or doplet.network_ids or []]
            doplet.ip_addresses = payload.get("ip_addresses") or doplet.ip_addresses or []
            doplet.storage_backend = payload.get("storage_backend") or doplet.storage_backend or host.primary_storage_backend or "files"
            doplet.security_tier = payload.get("security_tier") or doplet.security_tier
            doplet.exposure_mode = payload.get("exposure_mode") or doplet.exposure_mode
            doplet.bootstrap_user = payload.get("bootstrap_user") or doplet.bootstrap_user
            doplet.ssh_public_keys = payload.get("ssh_public_keys") or doplet.ssh_public_keys or []
            doplet.gpu_assignments = requested_gpu_assignments
            metadata = dict(doplet.metadata_json or {})
            metadata.update(payload.get("metadata_json") or {})
            auth_mode = str((payload.get("metadata_json") or {}).get("auth_mode") or metadata.get("auth_mode") or "").strip()
            bootstrap_password = str(
                payload.get("bootstrap_password")
                or metadata.get("bootstrap_password")
                or ""
            ).strip()
            if not bootstrap_password:
                bootstrap_password = _make_bootstrap_password()
            if bootstrap_password:
                metadata["bootstrap_password"] = bootstrap_password
            if auth_mode in {"password", "ssh", "password+ssh"}:
                metadata["auth_mode"] = auth_mode
            if "backup_policy" in payload:
                metadata["backup_policy"] = dict(payload.get("backup_policy") or {})
            metadata["gpu_preflight"] = gpu_preflight
            doplet.metadata_json = metadata
            session.flush()
            self._audit(session, actor=actor, action="doplet.upsert", target_type="doplet", target_id=str(doplet.id), summary=f"Upserted doplet {doplet.name}")
            return self._doplet_payload(doplet)

    def queue_doplet_create(self, doplet_id: int, *, actor: str = "system") -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            doplet = session.get(Doplet, doplet_id)
            if not doplet:
                raise KeyError(f"Unknown doplet: {doplet_id}")
            host = session.get(HostNode, doplet.host_id)
            image = session.get(ImageRecord, doplet.image_id) if doplet.image_id else None
            network = session.get(Network, doplet.primary_network_id) if doplet.primary_network_id else None
            if not host or not image:
                raise ValueError("Doplet create requires a host and image.")
            host_payload = self._host_payload(host)
            resources = ((host_payload.get("inventory") or {}).get("resources") or {})
            if host.status != "ready" and not resources.get("virtualization_ready"):
                raise ValueError("Prepare this host first. Save the host, capture inventory, and run Prepare Host before creating a Doplet.")
            task = TaskRecord(
                task_type="create-doplet",
                target_type="doplet",
                target_id=str(doplet.id),
                status="planned",
                command_plan=doplet_create_plan(
                    host_payload,
                    self._doplet_payload(doplet),
                    self._image_payload(image),
                    self._network_payload(network) if network else None,
                ),
            )
            task.result_payload = self._task_security_payload(task.task_type, task.target_type, task.target_id, task.command_plan or [])
            session.add(task)
            doplet.status = "provisioning"
            session.flush()
            self._audit(session, actor=actor, action="task.queue", target_type="task", target_id=str(task.id), summary=f"Queued create for {doplet.name}")
            return self._task_payload(task)

    def _load_manageable_doplet(self, session: Session, doplet_id: int, *, allow_deleted: bool = False) -> Doplet:
        doplet = session.get(Doplet, doplet_id)
        if not doplet:
            raise KeyError(f"Unknown doplet: {doplet_id}")
        if not allow_deleted and str(doplet.status or "").strip().lower() == "deleted":
            raise ValueError(f"Doplet {doplet.name} was deleted and can no longer be managed.")
        return doplet

    def queue_doplet_lifecycle(self, doplet_id: int, action: str, *, actor: str = "system") -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            doplet = self._load_manageable_doplet(session, doplet_id)
            host = session.get(HostNode, doplet.host_id)
            task = TaskRecord(
                task_type=f"doplet-{action}",
                target_type="doplet",
                target_id=str(doplet.id),
                status="planned",
                command_plan=doplet_lifecycle_plan(self._host_payload(host), doplet.slug, action),
            )
            task.result_payload = self._task_security_payload(task.task_type, task.target_type, task.target_id, task.command_plan or [])
            session.add(task)
            session.flush()
            self._audit(session, actor=actor, action="task.queue", target_type="task", target_id=str(task.id), summary=f"Queued {action} for {doplet.name}")
            return self._task_payload(task)

    def open_doplet_terminal(self, doplet_id: int, *, actor: str = "system") -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            doplet = self._load_manageable_doplet(session, doplet_id)
            host = session.get(HostNode, doplet.host_id)
            if not host:
                raise KeyError(f"Unknown host for doplet: {doplet.host_id}")
            details = open_doplet_terminal(self._host_payload(host), self._doplet_payload(doplet))
            self._persist_doplet_terminal_details(doplet, details)
            self._audit(
                session,
                actor=actor,
                action="doplet.open-terminal",
                target_type="doplet",
                target_id=str(doplet.id),
                summary=f"Opened terminal for {doplet.name}",
                details={key: value for key, value in details.items() if key != "preview_command"},
            )
            return details

    def describe_doplet_terminal(self, doplet_id: int, *, establish_localhost_endpoint: bool = True) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            doplet = self._load_manageable_doplet(session, doplet_id)
            host = session.get(HostNode, doplet.host_id)
            if not host:
                raise KeyError(f"Unknown host for doplet: {doplet.host_id}")
            details = describe_doplet_terminal(
                self._host_payload(host),
                self._doplet_payload(doplet),
                establish_localhost_endpoint=establish_localhost_endpoint,
            )
            self._persist_doplet_terminal_details(doplet, details)
            return details

    def _persist_doplet_terminal_details(self, doplet: Doplet, details: dict[str, Any]) -> None:
        ips = [str(item).strip() for item in details.get("ip_addresses") or [] if str(item).strip()]
        if ips and ips != list(doplet.ip_addresses or []):
            doplet.ip_addresses = ips
        metadata = dict(doplet.metadata_json or {})
        access = dict(metadata.get("access") or {})
        access.update(
            {
                "label": str(details.get("access_label") or ""),
                "note": str(details.get("access_note") or ""),
                "transport": str(details.get("transport") or ""),
                "target": str(details.get("target") or ""),
                "preview_command": str(details.get("preview_command") or ""),
                "forward_host": str(details.get("forward_host") or ""),
                "forward_port": int(details.get("forward_port") or 0) if str(details.get("forward_port") or "").strip() else 0,
                "ip_addresses": ips or list(doplet.ip_addresses or []),
                "updated_at": self._now().isoformat(),
            }
        )
        metadata["access"] = access
        doplet.metadata_json = metadata

    def queue_resize_doplet(self, doplet_id: int, payload: dict[str, Any], *, actor: str = "system") -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            doplet = self._load_manageable_doplet(session, doplet_id)
            host = session.get(HostNode, doplet.host_id)
            target_vcpu = int(payload.get("vcpu") or doplet.vcpu)
            target_ram_mb = int(payload.get("ram_mb") or doplet.ram_mb)
            target_disk_gb = int(payload.get("disk_gb") or doplet.disk_gb)
            peer_records = session.scalars(select(Doplet).where(Doplet.host_id == doplet.host_id)).all()
            peer_payloads = [self._doplet_payload(item) for item in peer_records if item.id != doplet.id]
            capacity = capacity_summary(self._host_payload(host), peer_payloads)
            okay, errors = can_allocate(
                capacity,
                {
                    "vcpu": target_vcpu,
                    "ram_mb": target_ram_mb,
                    "disk_gb": target_disk_gb,
                    "gpu_assignments": doplet.gpu_assignments or [],
                },
            )
            if not okay:
                raise ValueError(" ".join(errors))
            command_plan = resize_plan(
                self._host_payload(host),
                self._doplet_payload(doplet),
                target_vcpu=target_vcpu,
                target_ram_mb=target_ram_mb,
                target_disk_gb=target_disk_gb,
            )
            task = TaskRecord(
                task_type="resize-doplet",
                target_type="doplet",
                target_id=str(doplet.id),
                status="planned",
                command_plan=command_plan,
                result_payload={
                    **self._task_security_payload("resize-doplet", "doplet", str(doplet.id), command_plan),
                    "resize_spec": {"vcpu": target_vcpu, "ram_mb": target_ram_mb, "disk_gb": target_disk_gb},
                },
            )
            session.add(task)
            session.flush()
            self._audit(session, actor=actor, action="task.queue", target_type="task", target_id=str(task.id), summary=f"Queued resize for {doplet.name}")
            return self._task_payload(task)

    def queue_backup(
        self,
        doplet_id: int,
        provider_ids: list[int] | None = None,
        *,
        actor: str = "system",
        backup_type: str = "manual",
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            doplet = self._load_manageable_doplet(session, doplet_id)
            host = session.get(HostNode, doplet.host_id)
            providers = session.scalars(select(BackupProvider).where(BackupProvider.enabled.is_(True))).all()
            if provider_ids:
                providers = [provider for provider in providers if provider.id in provider_ids]
            command_plan = backup_plan(
                self._host_payload(host),
                self._doplet_payload(doplet),
                [self._provider_payload(item) for item in providers],
            )
            task = TaskRecord(
                task_type="backup-doplet",
                target_type="doplet",
                target_id=str(doplet.id),
                status="planned",
                command_plan=command_plan,
                result_payload=self._task_security_payload("backup-doplet", "doplet", str(doplet.id), command_plan),
            )
            session.add(task)
            session.flush()
            session.add(
                BackupRecord(
                    doplet_id=doplet.id,
                    provider_id=providers[0].id if providers else None,
                    backup_type=backup_type,
                    status="planned",
                    manifest={"providers": [provider.slug for provider in providers], "queued_task": task.id},
                )
            )
            self._audit(session, actor=actor, action="task.queue", target_type="task", target_id=str(task.id), summary=f"Queued backup for {doplet.name}")
            return self._task_payload(task)

    def queue_snapshot(self, doplet_id: int, snapshot_name: str | None = None, *, actor: str = "system") -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            doplet = self._load_manageable_doplet(session, doplet_id)
            host = session.get(HostNode, doplet.host_id)
            generated_name = snapshot_name or f"{doplet.slug}-{security_now().strftime('%Y%m%d-%H%M%S')}"
            command_plan = snapshot_plan(self._host_payload(host), self._doplet_payload(doplet), generated_name)
            task = TaskRecord(
                task_type="snapshot-doplet",
                target_type="doplet",
                target_id=str(doplet.id),
                status="planned",
                command_plan=command_plan,
                result_payload={
                    **self._task_security_payload("snapshot-doplet", "doplet", str(doplet.id), command_plan),
                    "snapshot_name": generated_name,
                },
            )
            session.add(task)
            session.flush()
            snapshot = SnapshotRecord(
                doplet_id=doplet.id,
                name=generated_name,
                status="planned",
                artifact_reference=str(task.command_plan[0].get("artifact_reference", "")),
                metadata_json={"queued_task": task.id, "storage_backend": doplet.storage_backend},
            )
            session.add(snapshot)
            session.flush()
            task.result_payload = {
                **(task.result_payload or {}),
                "snapshot_id": snapshot.id,
                "snapshot_name": generated_name,
            }
            self._audit(session, actor=actor, action="task.queue", target_type="task", target_id=str(task.id), summary=f"Queued snapshot for {doplet.name}")
            return self._task_payload(task)

    def queue_clone(self, source_doplet_id: int, payload: dict[str, Any], *, actor: str = "system") -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            source = self._load_manageable_doplet(session, source_doplet_id)
            requested_host_id = int(payload.get("host_id") or source.host_id)
            host = session.get(HostNode, requested_host_id)
            network_id = int(payload.get("primary_network_id") or source.primary_network_id or 0) or None
            network = session.get(Network, network_id) if network_id else None
            clone_name = payload.get("name") or f"{source.name} Clone"
            clone_slug = _slugify(payload.get("slug") or f"{source.slug}-clone")
            requested_gpu_assignments = payload.get("gpu_assignments") or source.gpu_assignments or []
            peer_records = session.scalars(select(Doplet).where(Doplet.host_id == requested_host_id)).all()
            peer_payloads = [self._doplet_payload(item) for item in peer_records]
            capacity = capacity_summary(self._host_payload(host), peer_payloads)
            if any(capacity.get("totals", {}).values()):
                okay, errors = can_allocate(
                    capacity,
                    {
                        "vcpu": int(payload.get("vcpu") or source.vcpu),
                        "ram_mb": int(payload.get("ram_mb") or source.ram_mb),
                        "disk_gb": int(payload.get("disk_gb") or source.disk_gb),
                        "gpu_assignments": requested_gpu_assignments,
                    },
                )
                if not okay:
                    raise ValueError(" ".join(errors))
            target = Doplet(
                slug=clone_slug,
                name=clone_name,
                host_id=requested_host_id,
                image_id=source.image_id,
                flavor_id=source.flavor_id,
                status="provisioning",
                vcpu=int(payload.get("vcpu") or source.vcpu),
                ram_mb=int(payload.get("ram_mb") or source.ram_mb),
                disk_gb=int(payload.get("disk_gb") or source.disk_gb),
                primary_network_id=network_id,
                network_ids=payload.get("network_ids") or source.network_ids or [],
                ip_addresses=[],
                storage_backend=payload.get("storage_backend") or source.storage_backend,
                security_tier=payload.get("security_tier") or source.security_tier,
                exposure_mode=payload.get("exposure_mode") or source.exposure_mode,
                bootstrap_user=payload.get("bootstrap_user") or source.bootstrap_user,
                ssh_public_keys=payload.get("ssh_public_keys") or source.ssh_public_keys or [],
                gpu_assignments=requested_gpu_assignments,
                metadata_json={"source_doplet_id": source.id, "clone_type": "live-clone"},
            )
            session.add(target)
            session.flush()
            task = TaskRecord(
                task_type="clone-doplet",
                target_type="doplet",
                target_id=str(target.id),
                status="planned",
                command_plan=clone_plan(
                    self._host_payload(host),
                    self._doplet_payload(source),
                    self._doplet_payload(target),
                    self._network_payload(network) if network else None,
                ),
                result_payload={"source_doplet_id": source.id},
            )
            task.result_payload = {
                **self._task_security_payload(task.task_type, task.target_type, task.target_id, task.command_plan or []),
                **(task.result_payload or {}),
            }
            session.add(task)
            session.flush()
            self._audit(session, actor=actor, action="task.queue", target_type="task", target_id=str(task.id), summary=f"Queued clone from {source.name} to {target.name}")
            return self._task_payload(task)

    def queue_restore_snapshot(self, snapshot_id: int, payload: dict[str, Any] | None = None, *, actor: str = "system") -> dict[str, Any]:
        payload = payload or {}
        with session_scope(self.session_factory) as session:
            snapshot = session.get(SnapshotRecord, snapshot_id)
            if not snapshot:
                raise KeyError(f"Unknown snapshot: {snapshot_id}")
            source = session.get(Doplet, snapshot.doplet_id)
            if not source:
                raise KeyError(f"Source doplet missing for snapshot: {snapshot_id}")

            target_id = payload.get("target_doplet_id")
            in_place = bool(target_id)
            if target_id:
                target = session.get(Doplet, int(target_id))
                if not target:
                    raise KeyError(f"Unknown restore target: {target_id}")
            else:
                requested_host_id = int(payload.get("host_id") or source.host_id)
                requested_gpu_assignments = payload.get("gpu_assignments") or source.gpu_assignments or []
                peer_records = session.scalars(select(Doplet).where(Doplet.host_id == requested_host_id)).all()
                peer_payloads = [self._doplet_payload(item) for item in peer_records]
                host = session.get(HostNode, requested_host_id)
                capacity = capacity_summary(self._host_payload(host), peer_payloads)
                if any(capacity.get("totals", {}).values()):
                    okay, errors = can_allocate(
                        capacity,
                        {
                            "vcpu": int(payload.get("vcpu") or source.vcpu),
                            "ram_mb": int(payload.get("ram_mb") or source.ram_mb),
                            "disk_gb": int(payload.get("disk_gb") or source.disk_gb),
                            "gpu_assignments": requested_gpu_assignments,
                        },
                    )
                    if not okay:
                        raise ValueError(" ".join(errors))
                target = Doplet(
                    slug=_slugify(payload.get("name") or payload.get("slug") or f"{source.slug}-restore"),
                    name=payload.get("name") or f"{source.name} Restore",
                    host_id=requested_host_id,
                    image_id=source.image_id,
                    flavor_id=source.flavor_id,
                    status="provisioning",
                    vcpu=int(payload.get("vcpu") or source.vcpu),
                    ram_mb=int(payload.get("ram_mb") or source.ram_mb),
                    disk_gb=int(payload.get("disk_gb") or source.disk_gb),
                    primary_network_id=int(payload.get("primary_network_id") or source.primary_network_id or 0) or None,
                    network_ids=payload.get("network_ids") or source.network_ids or [],
                    ip_addresses=[],
                    storage_backend=payload.get("storage_backend") or source.storage_backend,
                    security_tier=payload.get("security_tier") or source.security_tier,
                    exposure_mode=payload.get("exposure_mode") or source.exposure_mode,
                    bootstrap_user=payload.get("bootstrap_user") or source.bootstrap_user,
                    ssh_public_keys=payload.get("ssh_public_keys") or source.ssh_public_keys or [],
                    gpu_assignments=requested_gpu_assignments,
                    metadata_json={"restored_from_snapshot_id": snapshot.id},
                )
                session.add(target)
                session.flush()

            host = session.get(HostNode, target.host_id)
            network = session.get(Network, target.primary_network_id) if target.primary_network_id else None
            task = TaskRecord(
                task_type="restore-doplet",
                target_type="doplet",
                target_id=str(target.id),
                status="planned",
                command_plan=restore_plan(
                    self._host_payload(host),
                    self._doplet_payload(target),
                    self._snapshot_payload(snapshot),
                    self._network_payload(network) if network else None,
                    in_place=in_place,
                ),
                result_payload={"snapshot_id": snapshot.id, "in_place": in_place, "source_doplet_id": source.id},
            )
            task.result_payload = {
                **self._task_security_payload(task.task_type, task.target_type, task.target_id, task.command_plan or []),
                **(task.result_payload or {}),
            }
            session.add(task)
            session.flush()
            self._audit(session, actor=actor, action="task.queue", target_type="task", target_id=str(task.id), summary=f"Queued restore from snapshot {snapshot.name} to {target.name}")
            return self._task_payload(task)

    def delete_doplet(self, doplet_id: int, *, actor: str = "system") -> None:
        with session_scope(self.session_factory) as session:
            doplet = session.get(Doplet, doplet_id)
            if not doplet:
                raise KeyError(f"Unknown doplet: {doplet_id}")
            name = doplet.name
            session.delete(doplet)
            self._audit(session, actor=actor, action="doplet.delete", target_type="doplet", target_id=str(doplet_id), summary=f"Deleted doplet record {name}")

    def force_delete_doplet(self, doplet_id: int, *, actor: str = "system") -> dict[str, Any]:
        cancelled_tasks: list[int] = []
        warnings: list[str] = []
        delete_task_payload: dict[str, Any] | None = None

        with session_scope(self.session_factory) as session:
            doplet = session.get(Doplet, doplet_id)
            if not doplet:
                raise KeyError(f"Unknown doplet: {doplet_id}")
            name = doplet.name
            active_tasks = session.scalars(
                select(TaskRecord)
                .where(TaskRecord.target_type == "doplet")
                .where(TaskRecord.target_id == str(doplet_id))
                .where(TaskRecord.status.in_(["planned", "queued", "running", "cancel-requested"]))
                .order_by(TaskRecord.created_at.desc())
            ).all()
            active_task_ids = [int(task.id) for task in active_tasks if task.task_type != "doplet-delete"]

        for task_id in active_task_ids:
            try:
                self.cancel_task(task_id, actor=actor)
                cancelled_tasks.append(task_id)
            except Exception as exc:
                warnings.append(f"Could not cancel task {task_id}: {exc}")

        try:
            delete_task_payload = self.queue_doplet_lifecycle(doplet_id, "delete", actor=actor)
            try:
                self.run_task(int(delete_task_payload["id"]), actor=actor, dry_run=False)
            except Exception as exc:
                warnings.append(f"Runtime delete step reported an error: {exc}")
        except Exception as exc:
            warnings.append(f"Could not queue runtime delete: {exc}")

        with session_scope(self.session_factory) as session:
            doplet = session.get(Doplet, doplet_id)
            if not doplet:
                raise KeyError(f"Unknown doplet: {doplet_id}")
            metadata = dict(doplet.metadata_json or {})
            deleted_at = self._now().isoformat()
            deletion_history = list(metadata.get("deletion_history") or [])
            deletion_history.append(
                {
                    "deleted_at": deleted_at,
                    "actor": actor,
                    "cancelled_task_ids": cancelled_tasks,
                    "warnings": warnings,
                }
            )
            metadata["deleted_at"] = deleted_at
            metadata["deleted_by"] = actor
            metadata["deletion_history"] = deletion_history
            metadata["delete_warnings"] = warnings
            doplet.metadata_json = metadata
            doplet.status = "deleted"
            doplet.ip_addresses = []
            self._audit(
                session,
                actor=actor,
                action="doplet.soft-delete",
                target_type="doplet",
                target_id=str(doplet_id),
                summary=f"Soft-deleted doplet {name}",
                details={"cancelled_task_ids": cancelled_tasks, "warnings": warnings},
            )
            payload = self._doplet_payload(doplet)

        return {
            "doplet": payload,
            "cancelled_task_ids": cancelled_tasks,
            "warnings": warnings,
            "delete_task": delete_task_payload,
        }

    def delete_host(self, host_id: int, *, actor: str = "system") -> None:
        with session_scope(self.session_factory) as session:
            host = session.get(HostNode, host_id)
            if not host:
                raise KeyError(f"Unknown host: {host_id}")
            attached = session.query(Doplet).filter(Doplet.host_id == host_id).count()
            if attached:
                raise ValueError("Cannot delete a host while Doplets still target it.")
            name = host.name
            session.delete(host)
            self._audit(session, actor=actor, action="host.delete", target_type="host", target_id=str(host_id), summary=f"Deleted host {name}")

    def delete_network(self, network_id: int, *, actor: str = "system") -> None:
        with session_scope(self.session_factory) as session:
            network = session.get(Network, network_id)
            if not network:
                raise KeyError(f"Unknown network: {network_id}")
            in_use = session.query(Doplet).filter(Doplet.primary_network_id == network_id).count()
            if in_use:
                raise ValueError("Cannot delete a network while it is assigned as a primary network.")
            name = network.name
            session.delete(network)
            self._audit(session, actor=actor, action="network.delete", target_type="network", target_id=str(network_id), summary=f"Deleted network {name}")

    def delete_backup_provider(self, provider_id: int, *, actor: str = "system") -> None:
        with session_scope(self.session_factory) as session:
            provider = session.get(BackupProvider, provider_id)
            if not provider:
                raise KeyError(f"Unknown provider: {provider_id}")
            name = provider.name
            session.delete(provider)
            self._audit(session, actor=actor, action="provider.delete", target_type="provider", target_id=str(provider_id), summary=f"Deleted provider {name}")

    def cancel_task(self, task_id: int, *, actor: str = "system") -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            task = session.get(TaskRecord, task_id)
            if not task:
                raise KeyError(f"Unknown task: {task_id}")
            if task.status in {"planned", "queued"}:
                task.status = "cancelled"
                task.progress = max(int(task.progress or 0), 1)
                payload = dict(task.result_payload or {})
                payload["cancelled"] = True
                payload["cancelled_by"] = actor
                task.result_payload = payload
            elif task.status == "running":
                task.status = "cancel-requested"
                payload = dict(task.result_payload or {})
                payload["cancel_requested"] = True
                payload["cancel_requested_by"] = actor
                task.result_payload = payload
                with self._task_lock:
                    event = self._task_cancel_events.get(task_id)
                    if event is not None:
                        event.set()
            else:
                raise ValueError(f"Task {task_id} cannot be cancelled from status {task.status}.")
            self._audit(session, actor=actor, action="task.cancel", target_type="task", target_id=str(task.id), summary=f"Cancelled task {task.task_type}")
            return self._task_payload(task)

    def retry_task(self, task_id: int, *, actor: str = "system") -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            source = session.get(TaskRecord, task_id)
            if not source:
                raise KeyError(f"Unknown task: {task_id}")
            if source.status not in {"failed", "cancelled"}:
                raise ValueError("Only failed or cancelled tasks can be retried.")
            payload = dict(source.result_payload or {})
            payload.pop("results", None)
            payload.pop("error", None)
            payload["retry_of_task_id"] = source.id
            retry = TaskRecord(
                task_type=source.task_type,
                target_type=source.target_type,
                target_id=source.target_id,
                status="planned",
                command_plan=list(source.command_plan or []),
                result_payload=payload,
                requested_by_user_id=source.requested_by_user_id,
            )
            session.add(retry)
            session.flush()
            self._audit(session, actor=actor, action="task.retry", target_type="task", target_id=str(retry.id), summary=f"Retried task {source.task_type}")
            return self._task_payload(retry)

    def run_backup_scheduler(self, *, actor: str = "system") -> dict[str, Any]:
        now = self._now()
        due: list[tuple[int, dict[str, Any]]] = []
        with session_scope(self.session_factory) as session:
            doplets = session.scalars(select(Doplet).order_by(Doplet.name)).all()
            for doplet in doplets:
                policy = self._backup_policy(doplet)
                if not policy.get("enabled") or int(policy.get("schedule_minutes") or 0) <= 0:
                    continue
                last_scheduled_at = policy.get("last_scheduled_at")
                if last_scheduled_at:
                    try:
                        last_seen = datetime.fromisoformat(last_scheduled_at)
                    except ValueError:
                        last_seen = None
                    else:
                        if last_seen.tzinfo is None:
                            last_seen = last_seen.replace(tzinfo=timezone.utc)
                    if last_seen and (now - last_seen).total_seconds() < int(policy["schedule_minutes"]) * 60:
                        continue
                due.append((doplet.id, policy))
                policy["last_scheduled_at"] = now.isoformat()
                self._set_backup_policy(doplet, policy)
            self._audit(session, actor=actor, action="backup.scheduler_run", target_type="maintenance", target_id="backup-scheduler", summary=f"Found {len(due)} scheduled backups due")
        queued = [self.queue_backup(doplet_id, policy.get("provider_ids") or None, actor=actor, backup_type="scheduled") for doplet_id, policy in due]
        return {"queued": queued, "count": len(queued), "ran_at": now.isoformat()}

    def prune_backup_records(self, *, actor: str = "system") -> dict[str, Any]:
        deleted: list[dict[str, Any]] = []
        with session_scope(self.session_factory) as session:
            doplets = session.scalars(select(Doplet).order_by(Doplet.name)).all()
            for doplet in doplets:
                policy = self._backup_policy(doplet)
                retain_count = int(policy.get("retain_count") or 0)
                if retain_count <= 0:
                    continue
                records = session.scalars(
                    select(BackupRecord)
                    .where(BackupRecord.doplet_id == doplet.id)
                    .order_by(BackupRecord.created_at.desc())
                ).all()
                for record in records[retain_count:]:
                    self._delete_backup_artifacts(record, prune_remote=bool(policy.get("prune_remote")))
                    deleted.append(self._backup_payload(record))
                    session.delete(record)
            self._audit(session, actor=actor, action="backup.prune", target_type="maintenance", target_id="backups", summary=f"Pruned {len(deleted)} backup records")
        return {"deleted": deleted, "count": len(deleted)}

    def verify_backup_record(self, backup_id: int, *, actor: str = "system") -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            backup = session.get(BackupRecord, backup_id)
            if not backup:
                raise KeyError(f"Unknown backup: {backup_id}")
            uploads = list((backup.manifest or {}).get("uploads") or [])
            local_files = list((backup.manifest or {}).get("local_files") or [])
            checks: list[dict[str, Any]] = []
            for path in local_files:
                exists = Path(path).exists()
                checks.append({"target": path, "kind": "local", "ok": exists})
            for upload in uploads:
                checks.extend(self._verify_upload_reference(upload))
            ok = all(item.get("ok") for item in checks) if checks else False
            manifest = dict(backup.manifest or {})
            manifest["verification"] = {"checked_at": self._now().isoformat(), "ok": ok, "checks": checks}
            backup.manifest = manifest
            self._audit(session, actor=actor, action="backup.verify", target_type="backup", target_id=str(backup.id), summary=f"Verified backup {backup.id}")
            return {"backup": self._backup_payload(backup), "verification": manifest["verification"]}

    def host_acceptance_report(self, host_id: int) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            host = session.get(HostNode, host_id)
            if not host:
                raise KeyError(f"Unknown host: {host_id}")
            payload = self._host_payload(host)
            resources = (payload.get("inventory") or {}).get("resources") or {}
            snapshot = (payload.get("inventory") or {}).get("snapshot") or {}
            doplets = [self._doplet_payload(item) for item in session.scalars(select(Doplet).where(Doplet.host_id == host.id)).all()]
            backend = str(payload.get("primary_storage_backend") or "files")
            if backend == "files":
                storage_ok = bool(resources.get("disk_total_bytes") or resources.get("disk_total_gib"))
                storage_detail = f"File-backed qcow2 runtime root {(payload.get('config') or {}).get('runtime_root') or '/var/lib/vpsdash'}"
            elif backend == "zfs":
                storage_ok = bool(resources.get("zfs_pools"))
                storage_detail = f"ZFS pools detected: {len(resources.get('zfs_pools') or [])}"
            else:
                storage_ok = bool((resources.get("vgs") or {}).get("report"))
                storage_detail = f"LVM report present: {'yes' if storage_ok else 'no'}"
            checks = [
                {"name": "libvirt-ready", "ok": bool(resources.get("virtualization_ready")), "detail": "Libvirt inventory probe returned successfully."},
                {"name": "iommu", "ok": True, "detail": f"IOMMU groups detected: {resources.get('iommu_groups', 0)}. Needed for GPU passthrough, not for a basic Doplet."},
                {
                    "name": "storage-backend",
                    "ok": storage_ok,
                    "detail": storage_detail,
                },
                {"name": "gpu-inventory", "ok": True, "detail": f"GPU devices: {len(resources.get('gpu_devices') or [])}, mediated profiles: {len(resources.get('mediated_profiles') or [])}"},
                {"name": "host-agent", "ok": True, "detail": f"Agent endpoint: {(payload.get('config') or {}).get('agent_endpoint') or f'http://{self.config.host_agent_bind_host}:{self.config.host_agent_port}'}"},
                {"name": "doplets-modeled", "ok": True, "detail": f"Tracked Doplets on host: {len(doplets)}"},
            ]
            if payload.get("host_mode") in {"windows-local", "windows-remote", "windows-wsl-remote"}:
                wsl_probe = snapshot.get("wsl_list") or {}
                checks.insert(
                    1,
                    {
                        "name": "wsl-ready",
                        "ok": bool(wsl_probe.get("ok")),
                        "detail": f"WSL distro probe for {payload.get('wsl_distribution') or 'Ubuntu'} returned {'ok' if wsl_probe.get('ok') else 'an error'}.",
                    },
                )
            return {"host": self._host_payload_with_capacity(session, host, doplets), "checks": checks, "ok": all(item["ok"] for item in checks)}

    def run_task(self, task_id: int, *, actor: str = "system", dry_run: bool = False) -> dict[str, Any]:
        cancel_event = threading.Event()
        with self._task_lock:
            self._task_cancel_events[task_id] = cancel_event
        with session_scope(self.session_factory) as session:
            task = session.get(TaskRecord, task_id)
            if not task:
                with self._task_lock:
                    self._task_cancel_events.pop(task_id, None)
                raise KeyError(f"Unknown task: {task_id}")
            if task.status == "cancelled":
                with self._task_lock:
                    self._task_cancel_events.pop(task_id, None)
                return self._task_payload(task)
            host_payload = self._resolve_task_host_payload(session, task)
            command_plan = list(task.command_plan or [])
            existing_result_payload = dict(task.result_payload or {})
            task.status = "running"
            task.progress = 5
            task.result_payload = {
                **existing_result_payload,
                "dry_run": dry_run,
            }
            self._audit(session, actor=actor, action="task.start", target_type="task", target_id=str(task.id), summary=f"Started task {task.task_type}")

        def _progress_callback(completed: int, total: int, result: dict[str, Any], results_so_far: list[dict[str, Any]]) -> None:
            progress = min(95, max(10, int((completed / max(total, 1)) * 90)))
            with session_scope(self.session_factory) as inner_session:
                live_task = inner_session.get(TaskRecord, task_id)
                if not live_task:
                    return
                live_task.progress = progress
                live_task.log_output = "\n\n".join(
                    [
                        "\n".join(
                            line
                            for line in [
                                f"[{item.get('title', 'step')}]",
                                item.get("stdout", ""),
                                item.get("stderr", ""),
                            ]
                            if line
                        )
                        for item in results_so_far
                    ]
                )
                live_payload = dict(live_task.result_payload or {})
                live_payload["latest_step"] = result.get("title", "")
                live_payload["steps_completed"] = completed
                live_payload["steps_total"] = total
                live_task.result_payload = live_payload

        try:
            results = self.agent.execute_task_plan(
                host_payload,
                command_plan,
                dry_run=dry_run,
                signature=existing_result_payload.get("plan_signature", ""),
                policy=existing_result_payload.get("task_policy", task.task_type),
                target_type=task.target_type,
                target_id=task.target_id,
                progress_callback=_progress_callback,
                cancel_callback=cancel_event.is_set,
            )
        except PlanCancelled as exc:
            with session_scope(self.session_factory) as session:
                cancelled_task = session.get(TaskRecord, task_id)
                if not cancelled_task:
                    raise
                cancelled_task.status = "cancelled"
                cancelled_task.progress = max(int(cancelled_task.progress or 0), 10)
                cancelled_payload = dict(cancelled_task.result_payload or {})
                cancelled_payload["cancelled"] = True
                cancelled_payload["error"] = str(exc)
                cancelled_task.result_payload = cancelled_payload
                cancelled_task.log_output = "\n".join(part for part in [cancelled_task.log_output, str(exc)] if part)
                self._audit(
                    session,
                    actor=actor,
                    action="task.finish",
                    target_type="task",
                    target_id=str(cancelled_task.id),
                    summary=f"Finished task {cancelled_task.task_type} with status cancelled",
                    details={"dry_run": dry_run, "ok": False, "cancelled": True},
                )
                return self._task_payload(cancelled_task)
        except Exception as exc:
            with session_scope(self.session_factory) as session:
                failed_task = session.get(TaskRecord, task_id)
                if not failed_task:
                    raise
                failed_payload = dict(failed_task.result_payload or {})
                failed_payload["error"] = str(exc)
                failed_task.status = "failed"
                failed_task.progress = max(int(failed_task.progress or 0), 10)
                failed_task.result_payload = failed_payload
                failed_task.log_output = "\n".join(part for part in [failed_task.log_output, str(exc)] if part)
                self._audit(
                    session,
                    actor=actor,
                    action="task.finish",
                    target_type="task",
                    target_id=str(failed_task.id),
                    summary=f"Finished task {failed_task.task_type} with status failed",
                    details={"dry_run": dry_run, "ok": False, "exception": str(exc)},
                )
            raise
        finally:
            with self._task_lock:
                self._task_cancel_events.pop(task_id, None)
        ok = all(item.get("ok", False) for item in results) if results else True
        uploads: list[dict[str, Any]] = []

        with session_scope(self.session_factory) as session:
            task = session.get(TaskRecord, task_id)
            if not task:
                raise KeyError(f"Task disappeared: {task_id}")

            if task.task_type == "backup-doplet" and ok and not dry_run:
                uploads = self._finalize_backup_task(session, task, host_payload)

            previous_payload = dict(task.result_payload or {})
            task.status = "succeeded" if ok else "failed"
            task.progress = 100 if ok else max(int(task.progress or 0), 10)
            task.result_payload = {
                **previous_payload,
                "dry_run": dry_run,
                "results": results,
                "uploads": uploads,
            }
            task.log_output = "\n\n".join(
                [
                    "\n".join(
                        line
                        for line in [
                            f"[{item.get('title', 'step')}]",
                            item.get("stdout", ""),
                            item.get("stderr", ""),
                        ]
                        if line
                    )
                    for item in results
                ]
            )
            if not dry_run:
                self._apply_task_side_effects(session, task, ok)
            self._audit(
                session,
                actor=actor,
                action="task.finish",
                target_type="task",
                target_id=str(task.id),
                summary=f"Finished task {task.task_type} with status {task.status}",
                details={"dry_run": dry_run, "ok": ok},
            )
            return self._task_payload(task)

    def launch_task(self, task_id: int, *, actor: str = "system", dry_run: bool = False) -> dict[str, Any]:
        with self._task_lock:
            thread = self._task_threads.get(task_id)
            if thread and thread.is_alive():
                with session_scope(self.session_factory) as session:
                    task = session.get(TaskRecord, task_id)
                    if not task:
                        raise KeyError(f"Unknown task: {task_id}")
                    return self._task_payload(task)

            with session_scope(self.session_factory) as session:
                task = session.get(TaskRecord, task_id)
                if not task:
                    raise KeyError(f"Unknown task: {task_id}")
                if task.status in {"running", "queued", "cancel-requested"}:
                    return self._task_payload(task)
                if task.status == "cancelled":
                    raise ValueError(f"Task {task_id} was cancelled and must be retried instead.")
                task.status = "queued"
                task.progress = max(int(task.progress or 0), 1)
                payload = dict(task.result_payload or {})
                payload["queued_for_background"] = True
                payload["dry_run"] = dry_run
                task.result_payload = payload
                snapshot = self._task_payload(task)

            def _worker() -> None:
                try:
                    self.run_task(task_id, actor=actor, dry_run=dry_run)
                except Exception:
                    pass
                finally:
                    with self._task_lock:
                        self._task_threads.pop(task_id, None)

            thread = threading.Thread(target=_worker, daemon=True, name=f"task-{task_id}")
            self._task_threads[task_id] = thread
            thread.start()
            return snapshot

    def _resolve_task_host_payload(self, session: Session, task: TaskRecord) -> dict[str, Any]:
        if task.target_type == "host":
            host = session.get(HostNode, int(task.target_id))
        elif task.target_type == "doplet":
            doplet = session.get(Doplet, int(task.target_id))
            if not doplet:
                raise KeyError(f"Unknown doplet for task: {task.target_id}")
            host = session.get(HostNode, doplet.host_id)
        elif task.target_type == "network":
            network = session.get(Network, int(task.target_id))
            if not network:
                raise KeyError(f"Unknown network for task: {task.target_id}")
            host = session.get(HostNode, network.host_id)
        else:
            raise ValueError(f"Unsupported task target type: {task.target_type}")
        if not host:
            raise KeyError(f"Host not found for task {task.id}")
        return self._host_payload(host)

    def _apply_task_side_effects(self, session: Session, task: TaskRecord, ok: bool) -> None:
        if task.target_type == "host":
            host = session.get(HostNode, int(task.target_id))
            if host and task.task_type == "prepare-host":
                host.status = "ready" if ok else "error"
            return
        if task.target_type == "network":
            network = session.get(Network, int(task.target_id))
            if not network:
                return
            firewall = dict(network.firewall_policy or {})
            firewall["last_runtime_task"] = task.id
            firewall["runtime_status"] = "active" if ok and task.task_type == "apply-network" else firewall.get("runtime_status", "unknown")
            if ok and task.task_type == "delete-network-runtime":
                firewall["runtime_status"] = "deleted"
            network.firewall_policy = firewall
            return

        doplet = session.get(Doplet, int(task.target_id)) if task.target_type == "doplet" else None
        if not doplet:
            return
        if _doplet_is_deleted(doplet) and task.task_type != "doplet-delete":
            return
        if task.task_type == "create-doplet":
            doplet.status = "running" if ok else "error"
        elif task.task_type == "resize-doplet" and ok:
            resize_spec = dict((task.result_payload or {}).get("resize_spec") or {})
            doplet.vcpu = int(resize_spec.get("vcpu") or doplet.vcpu)
            doplet.ram_mb = int(resize_spec.get("ram_mb") or doplet.ram_mb)
            doplet.disk_gb = int(resize_spec.get("disk_gb") or doplet.disk_gb)
        elif task.task_type == "snapshot-doplet":
            snapshot_id = (task.result_payload or {}).get("snapshot_id")
            snapshot = session.get(SnapshotRecord, int(snapshot_id)) if snapshot_id else None
            if snapshot is None:
                snapshots = session.scalars(
                    select(SnapshotRecord)
                    .where(SnapshotRecord.doplet_id == doplet.id)
                    .order_by(SnapshotRecord.created_at.desc())
                ).all()
                snapshot_name = (task.result_payload or {}).get("snapshot_name")
                snapshot = next((item for item in snapshots if item.name == snapshot_name), snapshots[0] if snapshots else None)
            if snapshot:
                snapshot.status = "complete" if ok else "failed"
        elif task.task_type == "doplet-start":
            doplet.status = "running" if ok else doplet.status
        elif task.task_type == "doplet-shutdown":
            doplet.status = "stopped" if ok else doplet.status
        elif task.task_type == "doplet-reboot":
            doplet.status = "running" if ok else doplet.status
        elif task.task_type == "doplet-force-stop":
            doplet.status = "stopped" if ok else doplet.status
        elif task.task_type == "doplet-delete":
            doplet.status = "deleted" if ok else doplet.status
        elif task.task_type == "clone-doplet":
            doplet.status = "running" if ok else "error"
        elif task.task_type == "restore-doplet":
            doplet.status = "running" if ok else "error"
        elif task.task_type == "backup-doplet":
            backup = session.scalar(
                select(BackupRecord)
                .where(BackupRecord.doplet_id == doplet.id)
                .order_by(BackupRecord.created_at.desc())
            )
            if backup:
                backup.status = "complete" if ok else "failed"

    def _finalize_backup_task(self, session: Session, task: TaskRecord, host_payload: dict[str, Any]) -> list[dict[str, Any]]:
        metadata = next((step for step in task.command_plan or [] if step.get("artifact_path")), None)
        if not metadata:
            return []

        backup = session.scalar(
            select(BackupRecord)
            .where(BackupRecord.doplet_id == int(task.target_id))
            .order_by(BackupRecord.created_at.desc())
        )
        manifest = dict((backup.manifest if backup else {}) or {})
        provider_slugs = manifest.get("providers") or []
        providers = session.scalars(select(BackupProvider).where(BackupProvider.enabled.is_(True))).all()
        if provider_slugs:
            providers = [provider for provider in providers if provider.slug in provider_slugs]

        staged_dir = self.config.data_dir / "downloads" / f"task-{task.id}"
        staged_dir.mkdir(parents=True, exist_ok=True)
        local_files = self._materialize_backup_files(host_payload, metadata, staged_dir)
        uploads = [self._upload_backup_files(provider, local_files, task) for provider in providers]
        if backup:
            backup.status = "complete"
            backup.artifact_reference = ", ".join(upload.get("artifact_reference", "") for upload in uploads if upload.get("artifact_reference"))
            backup.manifest = {
                **manifest,
                "local_files": [str(path) for path in local_files],
                "uploads": uploads,
            }
            if local_files:
                try:
                    backup.size_bytes = sum(path.stat().st_size for path in local_files if path.exists())
                except OSError:
                    backup.size_bytes = 0
        return uploads

    def _materialize_backup_files(self, host_payload: dict[str, Any], metadata: dict[str, Any], target_dir: Path) -> list[Path]:
        target_dir.mkdir(parents=True, exist_ok=True)
        file_keys = ["artifact_path", "manifest_path", "domain_xml_path"]
        local_files: list[Path] = []
        for key in file_keys:
            remote_or_local = metadata.get(key)
            if not remote_or_local:
                continue
            filename = Path(str(remote_or_local).replace("~", "")).name
            destination = target_dir / filename
            if host_payload.get("ssh_host"):
                self.agent.materialize_file(host_payload, str(remote_or_local), destination)
            else:
                self.agent.materialize_file(host_payload, str(remote_or_local), destination)
            local_files.append(destination)
        return local_files

    def _upload_backup_files(self, provider: BackupProvider, local_files: list[Path], task: TaskRecord) -> dict[str, Any]:
        if provider.provider_type == "local":
            target_root = Path(provider.root_path or (self.config.data_dir / "provider-backups"))
            target_root.mkdir(parents=True, exist_ok=True)
            copied: list[str] = []
            for path in local_files:
                destination = target_root / path.name
                shutil.copy2(path, destination)
                copied.append(str(destination))
            return {
                "provider_id": provider.id,
                "provider_slug": provider.slug,
                "artifact_reference": ", ".join(copied),
                "files": copied,
            }

        client = boto3.client(
            "s3",
            endpoint_url=provider.endpoint or None,
            region_name=provider.region or None,
            aws_access_key_id=provider.access_key_id or None,
            aws_secret_access_key=decrypt_secret(self.config, provider.secret_key_encrypted) or None,
        )
        uploaded: list[str] = []
        for path in local_files:
            key = "/".join(part for part in [provider.root_path.strip("/"), f"task-{task.id}", path.name] if part)
            client.upload_file(str(path), provider.bucket, key)
            uploaded.append(f"s3://{provider.bucket}/{key}")
        return {
            "provider_id": provider.id,
            "provider_slug": provider.slug,
            "artifact_reference": ", ".join(uploaded),
            "files": uploaded,
        }

    def _delete_backup_artifacts(self, backup: BackupRecord, *, prune_remote: bool) -> None:
        manifest = dict(backup.manifest or {})
        for path in manifest.get("local_files") or []:
            try:
                candidate = Path(path)
                if candidate.exists():
                    candidate.unlink()
            except OSError:
                pass
        if not prune_remote:
            return
        for upload in manifest.get("uploads") or []:
            provider_slug = upload.get("provider_slug")
            if not provider_slug:
                continue
            with session_scope(self.session_factory) as session:
                provider = session.scalar(select(BackupProvider).where(BackupProvider.slug == provider_slug))
                if not provider or provider.provider_type == "local":
                    continue
                client = boto3.client(
                    "s3",
                    endpoint_url=provider.endpoint or None,
                    region_name=provider.region or None,
                    aws_access_key_id=provider.access_key_id or None,
                    aws_secret_access_key=decrypt_secret(self.config, provider.secret_key_encrypted) or None,
                )
                for ref in upload.get("files") or []:
                    if not str(ref).startswith("s3://"):
                        continue
                    without_prefix = str(ref)[5:]
                    bucket, _, key = without_prefix.partition("/")
                    if bucket and key:
                        try:
                            client.delete_object(Bucket=bucket, Key=key)
                        except Exception:
                            pass

    def _verify_upload_reference(self, upload: dict[str, Any]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        files = upload.get("files") or []
        provider_slug = upload.get("provider_slug") or ""
        with session_scope(self.session_factory) as session:
            provider = session.scalar(select(BackupProvider).where(BackupProvider.slug == provider_slug)) if provider_slug else None
        for ref in files:
            if str(ref).startswith("s3://") and provider and provider.provider_type == "s3":
                bucket, _, key = str(ref)[5:].partition("/")
                client = boto3.client(
                    "s3",
                    endpoint_url=provider.endpoint or None,
                    region_name=provider.region or None,
                    aws_access_key_id=provider.access_key_id or None,
                    aws_secret_access_key=decrypt_secret(self.config, provider.secret_key_encrypted) or None,
                )
                try:
                    client.head_object(Bucket=bucket, Key=key)
                    results.append({"target": ref, "kind": "s3", "ok": True})
                except Exception as exc:
                    results.append({"target": ref, "kind": "s3", "ok": False, "detail": str(exc)})
                continue
            exists = Path(str(ref)).exists()
            results.append({"target": ref, "kind": "local-provider", "ok": exists})
        return results

    def _task_security_payload(self, task_type: str, target_type: str, target_id: str, command_plan: list[dict[str, Any]]) -> dict[str, Any]:
        envelope = {
            "policy": task_type,
            "target_type": target_type,
            "target_id": str(target_id),
            "steps": command_plan,
        }
        return {
            "task_policy": task_type,
            "plan_signature": sign_json_payload(self.config, envelope),
        }

    def _host_payload_with_capacity(self, session: Session, host: HostNode, doplets: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        payload = self._host_payload(host)
        if doplets is None:
            doplets = [self._doplet_payload(item) for item in session.scalars(select(Doplet).where(Doplet.host_id == host.id)).all()]
        doplets = [item for item in doplets if int(item.get("host_id", 0) or 0) == int(host.id) and not _doplet_is_deleted(item)]
        payload["capacity"] = capacity_summary(payload, doplets)
        return payload

    def _user_payload(self, user: UserAccount) -> dict[str, Any]:
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "status": user.status,
            "mfa_enabled": user.mfa_enabled,
            "mfa_method": user.mfa_method,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            "created_at": user.created_at.isoformat(),
            "updated_at": user.updated_at.isoformat(),
        }

    def _host_payload(self, host: HostNode) -> dict[str, Any]:
        inventory = dict(host.inventory or {})
        if inventory.get("snapshot") and not inventory.get("resources"):
            inventory["resources"] = summarize_inventory(inventory.get("snapshot") or {})
        return {
            "id": host.id,
            "slug": host.slug,
            "name": host.name,
            "host_mode": host.host_mode,
            "distro": host.distro,
            "exposure_mode": host.exposure_mode,
            "primary_storage_backend": host.primary_storage_backend,
            "ssh_host": host.ssh_host,
            "ssh_user": host.ssh_user,
            "ssh_port": host.ssh_port,
            "wsl_distribution": host.wsl_distribution,
            "mixed_use_allowed": host.mixed_use_allowed,
            "mixed_use_warning_acknowledged": host.mixed_use_warning_acknowledged,
            "status": host.status,
            "inventory": inventory,
            "config": inventory.get("config") or {},
            "warnings": host.warnings or [],
            "notes": host.notes,
            "created_at": host.created_at.isoformat(),
            "updated_at": host.updated_at.isoformat(),
        }

    def _network_payload(self, network: Network | None) -> dict[str, Any]:
        if network is None:
            return {}
        return {
            "id": network.id,
            "host_id": network.host_id,
            "slug": network.slug,
            "name": network.name,
            "mode": network.mode,
            "cidr": network.cidr,
            "bridge_name": network.bridge_name,
            "nat_enabled": network.nat_enabled,
            "exposure_mode": network.exposure_mode,
            "firewall_policy": network.firewall_policy or {},
            "created_at": network.created_at.isoformat(),
            "updated_at": network.updated_at.isoformat(),
        }

    def _image_payload(self, image: ImageRecord) -> dict[str, Any]:
        return {
            "id": image.id,
            "slug": image.slug,
            "name": image.name,
            "distro": image.distro,
            "version": image.version,
            "source_url": image.source_url,
            "checksum": image.checksum,
            "size_bytes": image.size_bytes,
            "status": image.status,
        }

    def _flavor_payload(self, flavor: Flavor) -> dict[str, Any]:
        return {
            "id": flavor.id,
            "slug": flavor.slug,
            "name": flavor.name,
            "vcpu": flavor.vcpu,
            "ram_mb": flavor.ram_mb,
            "disk_gb": flavor.disk_gb,
            "gpu_mode": flavor.gpu_mode,
            "gpu_profile_id": flavor.gpu_profile_id,
        }

    def _doplet_payload(self, doplet: Doplet) -> dict[str, Any]:
        metadata = doplet.metadata_json or {}
        return {
            "id": doplet.id,
            "slug": doplet.slug,
            "name": doplet.name,
            "host_id": doplet.host_id,
            "image_id": doplet.image_id,
            "flavor_id": doplet.flavor_id,
            "status": doplet.status,
            "vcpu": doplet.vcpu,
            "ram_mb": doplet.ram_mb,
            "disk_gb": doplet.disk_gb,
            "primary_network_id": doplet.primary_network_id,
            "network_ids": doplet.network_ids or [],
            "ip_addresses": doplet.ip_addresses or [],
            "storage_backend": doplet.storage_backend,
            "security_tier": doplet.security_tier,
            "exposure_mode": doplet.exposure_mode,
            "bootstrap_user": doplet.bootstrap_user,
            "bootstrap_password": str(metadata.get("bootstrap_password") or ""),
            "ssh_public_keys": doplet.ssh_public_keys or [],
            "gpu_assignments": doplet.gpu_assignments or [],
            "metadata_json": metadata,
            "backup_policy": dict(metadata.get("backup_policy") or {}),
            "gpu_preflight": dict(metadata.get("gpu_preflight") or {}),
            "deleted_at": str(metadata.get("deleted_at") or ""),
            "deletion_history": list(metadata.get("deletion_history") or []),
            "delete_warnings": list(metadata.get("delete_warnings") or []),
            "created_at": doplet.created_at.isoformat(),
            "updated_at": doplet.updated_at.isoformat(),
        }

    def _provider_payload(self, provider: BackupProvider) -> dict[str, Any]:
        return {
            "id": provider.id,
            "slug": provider.slug,
            "name": provider.name,
            "provider_type": provider.provider_type,
            "endpoint": provider.endpoint,
            "bucket": provider.bucket,
            "region": provider.region,
            "root_path": provider.root_path,
            "access_key_id": provider.access_key_id,
            "quota_model": provider.quota_model or {},
            "enabled": provider.enabled,
            "policy_notes": provider.policy_notes,
            "has_secret": bool(provider.secret_key_encrypted),
            "created_at": provider.created_at.isoformat(),
            "updated_at": provider.updated_at.isoformat(),
        }

    def _backup_payload(self, backup: BackupRecord) -> dict[str, Any]:
        return {
            "id": backup.id,
            "doplet_id": backup.doplet_id,
            "provider_id": backup.provider_id,
            "backup_type": backup.backup_type,
            "status": backup.status,
            "artifact_reference": backup.artifact_reference,
            "manifest": backup.manifest or {},
            "size_bytes": backup.size_bytes,
            "created_at": backup.created_at.isoformat(),
            "updated_at": backup.updated_at.isoformat(),
        }

    def _snapshot_payload(self, snapshot: SnapshotRecord) -> dict[str, Any]:
        return {
            "id": snapshot.id,
            "doplet_id": snapshot.doplet_id,
            "name": snapshot.name,
            "status": snapshot.status,
            "artifact_reference": snapshot.artifact_reference,
            "metadata_json": snapshot.metadata_json or {},
            "created_at": snapshot.created_at.isoformat(),
            "updated_at": snapshot.updated_at.isoformat(),
        }

    def _task_payload(self, task: TaskRecord) -> dict[str, Any]:
        return {
            "id": task.id,
            "task_type": task.task_type,
            "target_type": task.target_type,
            "target_id": task.target_id,
            "status": task.status,
            "progress": task.progress,
            "command_plan": task.command_plan or [],
            "result_payload": task.result_payload or {},
            "log_output": task.log_output,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
        }

    def _audit_payload(self, event: AuditEvent) -> dict[str, Any]:
        return {
            "id": event.id,
            "created_at": event.created_at.isoformat(),
            "actor": event.actor,
            "action": event.action,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "summary": event.summary,
            "details": event.details or {},
        }


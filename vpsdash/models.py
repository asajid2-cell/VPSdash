from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class UserAccount(Base, TimestampMixin):
    __tablename__ = "user_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="owner")
    status: Mapped[str] = mapped_column(String(32), default="active")
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    mfa_method: Mapped[str] = mapped_column(String(32), default="email")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trusted_devices: Mapped[list["TrustedDevice"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    challenges: Mapped[list["LoginChallenge"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class TrustedDevice(Base, TimestampMixin):
    __tablename__ = "trusted_devices"
    __table_args__ = (UniqueConstraint("user_id", "device_fingerprint", name="uq_trusted_device"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_accounts.id", ondelete="CASCADE"))
    device_fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    device_name: Mapped[str] = mapped_column(String(255), default="Unknown device")
    trust_token_hash: Mapped[str] = mapped_column(String(255))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user: Mapped[UserAccount] = relationship(back_populates="trusted_devices")


class LoginChallenge(Base, TimestampMixin):
    __tablename__ = "login_challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_accounts.id", ondelete="CASCADE"))
    challenge_type: Mapped[str] = mapped_column(String(64), default="email-mfa")
    device_fingerprint: Mapped[str] = mapped_column(String(128))
    verification_code_hash: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user: Mapped[UserAccount] = relationship(back_populates="challenges")


class HostNode(Base, TimestampMixin):
    __tablename__ = "host_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    host_mode: Mapped[str] = mapped_column(String(32), default="linux-hypervisor")
    distro: Mapped[str] = mapped_column(String(64), default="ubuntu-server-lts")
    exposure_mode: Mapped[str] = mapped_column(String(32), default="lan-vpn-only")
    primary_storage_backend: Mapped[str] = mapped_column(String(32), default="files")
    ssh_host: Mapped[str] = mapped_column(String(255), default="")
    ssh_user: Mapped[str] = mapped_column(String(64), default="")
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    wsl_distribution: Mapped[str] = mapped_column(String(64), default="Ubuntu")
    mixed_use_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    mixed_use_warning_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    inventory: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")


class Network(Base, TimestampMixin):
    __tablename__ = "networks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("host_nodes.id", ondelete="CASCADE"))
    slug: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255))
    mode: Mapped[str] = mapped_column(String(32), default="nat")
    cidr: Mapped[str] = mapped_column(String(64), default="")
    bridge_name: Mapped[str] = mapped_column(String(64), default="")
    nat_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    exposure_mode: Mapped[str] = mapped_column(String(32), default="lan-vpn-only")
    firewall_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ImageRecord(Base, TimestampMixin):
    __tablename__ = "images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    distro: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str] = mapped_column(String(1024), default="")
    checksum: Mapped[str] = mapped_column(String(255), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="available")


class Flavor(Base, TimestampMixin):
    __tablename__ = "flavors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    vcpu: Mapped[int] = mapped_column(Integer)
    ram_mb: Mapped[int] = mapped_column(Integer)
    disk_gb: Mapped[int] = mapped_column(Integer)
    gpu_mode: Mapped[str] = mapped_column(String(64), default="none")
    gpu_profile_id: Mapped[str] = mapped_column(String(128), default="")


class Doplet(Base, TimestampMixin):
    __tablename__ = "doplets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    host_id: Mapped[int] = mapped_column(ForeignKey("host_nodes.id", ondelete="CASCADE"))
    image_id: Mapped[int | None] = mapped_column(ForeignKey("images.id", ondelete="SET NULL"), nullable=True)
    flavor_id: Mapped[int | None] = mapped_column(ForeignKey("flavors.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    vcpu: Mapped[int] = mapped_column(Integer, default=1)
    ram_mb: Mapped[int] = mapped_column(Integer, default=1024)
    disk_gb: Mapped[int] = mapped_column(Integer, default=20)
    primary_network_id: Mapped[int | None] = mapped_column(ForeignKey("networks.id", ondelete="SET NULL"), nullable=True)
    network_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    ip_addresses: Mapped[list[str]] = mapped_column(JSON, default=list)
    storage_backend: Mapped[str] = mapped_column(String(32), default="files")
    security_tier: Mapped[str] = mapped_column(String(32), default="standard")
    exposure_mode: Mapped[str] = mapped_column(String(32), default="lan-vpn-only")
    bootstrap_user: Mapped[str] = mapped_column(String(64), default="ubuntu")
    ssh_public_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    gpu_assignments: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class BackupProvider(Base, TimestampMixin):
    __tablename__ = "backup_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    provider_type: Mapped[str] = mapped_column(String(32), default="local")
    endpoint: Mapped[str] = mapped_column(String(1024), default="")
    bucket: Mapped[str] = mapped_column(String(255), default="")
    region: Mapped[str] = mapped_column(String(128), default="")
    root_path: Mapped[str] = mapped_column(String(1024), default="")
    access_key_id: Mapped[str] = mapped_column(String(255), default="")
    secret_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    quota_model: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    policy_notes: Mapped[str] = mapped_column(Text, default="")


class BackupRecord(Base, TimestampMixin):
    __tablename__ = "backup_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doplet_id: Mapped[int] = mapped_column(ForeignKey("doplets.id", ondelete="CASCADE"))
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("backup_providers.id", ondelete="SET NULL"), nullable=True)
    backup_type: Mapped[str] = mapped_column(String(32), default="manual")
    status: Mapped[str] = mapped_column(String(32), default="planned")
    artifact_reference: Mapped[str] = mapped_column(String(2048), default="")
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)


class SnapshotRecord(Base, TimestampMixin):
    __tablename__ = "snapshot_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doplet_id: Mapped[int] = mapped_column(ForeignKey("doplets.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="planned")
    artifact_reference: Mapped[str] = mapped_column(String(2048), default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class TaskRecord(Base, TimestampMixin):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_type: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str] = mapped_column(String(64), default="")
    target_id: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    command_plan: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    result_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    log_output: Mapped[str] = mapped_column(Text, default="")
    requested_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    actor: Mapped[str] = mapped_column(String(255), default="system")
    action: Mapped[str] = mapped_column(String(255))
    target_type: Mapped[str] = mapped_column(String(64), default="")
    target_id: Mapped[str] = mapped_column(String(128), default="")
    summary: Mapped[str] = mapped_column(String(512))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


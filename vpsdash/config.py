from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _env(*names: str, default: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return value
    return default


def _read_or_create_secret(path: Path, *, bytes_length: int = 32) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    value = secrets.token_urlsafe(bytes_length)
    path.write_text(value, encoding="utf-8")
    return value


@dataclass(slots=True)
class PlatformConfig:
    root: Path
    database_url: str
    flask_secret_key: str
    credential_key: str
    agent_secret_key: str
    public_base_url: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_sender: str
    smtp_use_tls: bool
    email_login_verification_enabled: bool
    login_mfa_ttl_minutes: int
    recent_mfa_minutes: int
    lan_only_default: bool
    default_host_distro: str
    host_agent_bind_host: str
    host_agent_port: int

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def outbox_dir(self) -> Path:
        return self.data_dir / "outbox"


def load_config(root: Path) -> PlatformConfig:
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    legacy_db_path = data_dir / "vps_harmonizer.db"
    default_db_path = data_dir / "vpsdash.db"
    active_db_path = default_db_path
    if not default_db_path.exists() and legacy_db_path.exists():
        try:
            legacy_db_path.replace(default_db_path)
        except PermissionError:
            active_db_path = legacy_db_path
        else:
            active_db_path = default_db_path
    default_db = f"sqlite:///{active_db_path.as_posix()}"
    flask_secret = _read_or_create_secret(data_dir / "flask_secret.key")
    credential_key = _read_or_create_secret(data_dir / "credentials.key")
    agent_secret = _read_or_create_secret(data_dir / "host_agent.key")

    return PlatformConfig(
        root=root,
        database_url=_env("VPSDASH_DATABASE_URL", "VPS_HARMONIZER_DATABASE_URL", default=default_db),
        flask_secret_key=_env("VPSDASH_SECRET_KEY", "VPS_HARMONIZER_SECRET_KEY", default=flask_secret),
        credential_key=_env("VPSDASH_CREDENTIAL_KEY", "VPS_HARMONIZER_CREDENTIAL_KEY", default=credential_key),
        agent_secret_key=_env("VPSDASH_AGENT_SECRET_KEY", "VPS_HARMONIZER_AGENT_SECRET_KEY", default=agent_secret),
        public_base_url=_env("VPSDASH_PUBLIC_BASE_URL", "VPS_HARMONIZER_PUBLIC_BASE_URL", default="http://127.0.0.1:8787"),
        smtp_host=_env("VPSDASH_SMTP_HOST", "VPS_HARMONIZER_SMTP_HOST", default=""),
        smtp_port=int(_env("VPSDASH_SMTP_PORT", "VPS_HARMONIZER_SMTP_PORT", default="587")),
        smtp_username=_env("VPSDASH_SMTP_USERNAME", "VPS_HARMONIZER_SMTP_USERNAME", default=""),
        smtp_password=_env("VPSDASH_SMTP_PASSWORD", "VPS_HARMONIZER_SMTP_PASSWORD", default=""),
        smtp_sender=_env("VPSDASH_SMTP_SENDER", "VPS_HARMONIZER_SMTP_SENDER", default="alerts@vpsdash.local"),
        smtp_use_tls=_env("VPSDASH_SMTP_USE_TLS", "VPS_HARMONIZER_SMTP_USE_TLS", default="true").lower() not in {"0", "false", "no"},
        email_login_verification_enabled=_env("VPSDASH_EMAIL_LOGIN_VERIFICATION", "VPS_HARMONIZER_EMAIL_LOGIN_VERIFICATION", default="false").lower() in {"1", "true", "yes", "on"},
        login_mfa_ttl_minutes=int(_env("VPSDASH_MFA_TTL_MINUTES", "VPS_HARMONIZER_MFA_TTL_MINUTES", default="10")),
        recent_mfa_minutes=int(_env("VPSDASH_RECENT_MFA_MINUTES", "VPS_HARMONIZER_RECENT_MFA_MINUTES", default="15")),
        lan_only_default=_env("VPSDASH_LAN_ONLY_DEFAULT", "VPS_HARMONIZER_LAN_ONLY_DEFAULT", default="true").lower() not in {"0", "false", "no"},
        default_host_distro=_env("VPSDASH_DEFAULT_DISTRO", "VPS_HARMONIZER_DEFAULT_DISTRO", default="ubuntu-server-lts"),
        host_agent_bind_host=_env("VPSDASH_AGENT_BIND_HOST", "VPS_HARMONIZER_AGENT_BIND_HOST", default="127.0.0.1"),
        host_agent_port=int(_env("VPSDASH_AGENT_PORT", "VPS_HARMONIZER_AGENT_PORT", default="8791")),
    )


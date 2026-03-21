from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet
from werkzeug.security import check_password_hash, generate_password_hash

from .config import PlatformConfig


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    return check_password_hash(password_hash, password)


def make_verification_code(length: int = 6) -> str:
    digits = "0123456789"
    return "".join(secrets.choice(digits) for _ in range(length))


def hash_verification_code(value: str) -> str:
    return generate_password_hash(value)


def verify_code(code_hash: str, code: str) -> bool:
    return check_password_hash(code_hash, code)


def _fernet_from_config(config: PlatformConfig) -> Fernet:
    key_material = hashlib.sha256(config.credential_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key_material))


def encrypt_secret(config: PlatformConfig, value: str) -> str:
    if not value:
        return ""
    return _fernet_from_config(config).encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(config: PlatformConfig, value: str) -> str:
    if not value:
        return ""
    return _fernet_from_config(config).decrypt(value.encode("utf-8")).decode("utf-8")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def expires_in(minutes: int) -> datetime:
    return utc_now() + timedelta(minutes=minutes)


def make_device_fingerprint(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def make_trust_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(24)
    return token, generate_password_hash(token)


def verify_trust_token(token_hash: str, token: str) -> bool:
    return check_password_hash(token_hash, token)


def sanitize_device_payload(payload: dict[str, Any] | None) -> dict[str, str]:
    payload = payload or {}
    return {
        "fingerprint_source": str(payload.get("fingerprint_source") or "unknown-device"),
        "device_name": str(payload.get("device_name") or "Unknown device"),
        "user_agent": str(payload.get("user_agent") or ""),
        "ip_address": str(payload.get("ip_address") or ""),
    }


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sign_json_with_key(secret_key: str, payload: dict[str, Any]) -> str:
    message = _canonical_json(payload).encode("utf-8")
    key = secret_key.encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def verify_json_signature_with_key(secret_key: str, payload: dict[str, Any], signature: str) -> bool:
    if not signature:
        return False
    expected = sign_json_with_key(secret_key, payload)
    return hmac.compare_digest(expected, signature)


def sign_json_payload(config: PlatformConfig, payload: dict[str, Any]) -> str:
    return sign_json_with_key(config.credential_key, payload)


def verify_json_signature(config: PlatformConfig, payload: dict[str, Any], signature: str) -> bool:
    return verify_json_signature_with_key(config.credential_key, payload, signature)

from __future__ import annotations

from typing import Any


ROLE_RANK = {
    "viewer": 10,
    "operator": 20,
    "owner": 30,
}


def normalize_role(role: str | None) -> str:
    return (role or "viewer").strip().lower()


def has_role(user_or_role: dict[str, Any] | str | None, *allowed_roles: str) -> bool:
    current_role = normalize_role(user_or_role.get("role") if isinstance(user_or_role, dict) else user_or_role)
    if not allowed_roles:
        return current_role in ROLE_RANK
    current_rank = ROLE_RANK.get(current_role, 0)
    return any(current_rank >= ROLE_RANK.get(normalize_role(role), 0) for role in allowed_roles)

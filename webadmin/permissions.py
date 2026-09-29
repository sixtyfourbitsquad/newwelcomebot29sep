"""Server-side owner vs admin permissions for the web panel."""

from __future__ import annotations

from typing import Any, Mapping

OWNER_ONLY = frozenset(
    {
        "admins.manage",
        "security.manage",
        "channel.write",
        "audit.read",
        "queue.clear",
    }
)


def panel_role(raw_role: str | None) -> str:
    """Map stored roles onto owner or admin. Legacy moderator/support act as admin."""
    if (raw_role or "") == "owner":
        return "owner"
    return "admin"


def resolve_panel_access(
    telegram_id: int,
    env_admin_ids: list[int] | set[int],
    db_row: Mapping[str, Any] | None,
) -> tuple[bool, str]:
    """
    Allow the user when they are in ADMIN_USER_IDS or an active database admin.

    Env-only staff (no database row) are owners so the first operator is not locked out.
    An inactive database row is denied unless the id is still in ADMIN_USER_IDS.
    """
    in_env = int(telegram_id) in {int(x) for x in env_admin_ids}
    if db_row is None:
        return (True, "owner") if in_env else (False, "")
    active = bool(db_row.get("is_active", True))
    role = panel_role(str(db_row.get("role") or "admin"))
    if not active and not in_env:
        return False, ""
    return True, role


def role_allows(role: str, permission: str) -> bool:
    if role == "owner":
        return True
    if role == "admin" and permission not in OWNER_ONLY:
        return True
    return False

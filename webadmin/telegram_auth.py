"""Verify Telegram Login Widget payloads. https://core.telegram.org/widgets/login"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Mapping


class TelegramAuthError(Exception):
    """Login payload failed verification."""


def build_data_check_string(data: Mapping[str, Any]) -> str:
    """Alphabetical field=value lines, excluding hash. Values are stringified."""
    lines: list[str] = []
    for key in sorted(k for k in data.keys() if k != "hash"):
        value = data[key]
        if value is None:
            continue
        lines.append(f"{key}={value}")
    return "\n".join(lines)


def telegram_login_hash(data_check_string: str, bot_token: str) -> str:
    secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
    return hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_telegram_login(
    data: Mapping[str, Any],
    bot_token: str,
    *,
    max_age_seconds: int,
    now: int | None = None,
) -> dict[str, Any]:
    """
    Validate the widget hash and auth_date.

    Returns the verified identity. The caller must still check admin membership.
    The Telegram user id is taken only from this signed payload.
    """
    if not data or "hash" not in data:
        raise TelegramAuthError("missing hash")
    received = str(data.get("hash") or "")
    if not received:
        raise TelegramAuthError("missing hash")
    check = build_data_check_string(data)
    expected = telegram_login_hash(check, bot_token)
    if not hmac.compare_digest(expected, received):
        raise TelegramAuthError("invalid hash")
    try:
        auth_date = int(data["auth_date"])
        user_id = int(data["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TelegramAuthError("missing id or auth_date") from exc
    current = int(time.time()) if now is None else int(now)
    if auth_date > current + 60:
        raise TelegramAuthError("auth_date in the future")
    if current - auth_date > int(max_age_seconds):
        raise TelegramAuthError("expired")
    return {
        "id": user_id,
        "first_name": str(data.get("first_name") or ""),
        "last_name": str(data.get("last_name") or ""),
        "username": str(data.get("username") or ""),
        "photo_url": str(data.get("photo_url") or ""),
        "auth_date": auth_date,
    }

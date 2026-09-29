"""Webhook secret check and update-id claim helpers."""

from __future__ import annotations

import hmac
from typing import Any


def webhook_secret_ok(*, expected: str | None, provided: str | None, required: bool) -> bool:
    """
    When a secret is configured, the header must match.

    When it is not configured, requests are accepted unless `required` is set.
    """
    if expected:
        if not provided:
            return False
        return hmac.compare_digest(provided, expected)
    if required:
        return False
    return True


def update_type_name(data: dict[str, Any]) -> str:
    for key in (
        "message",
        "edited_message",
        "channel_post",
        "edited_channel_post",
        "callback_query",
        "chat_join_request",
        "chat_member",
        "my_chat_member",
        "inline_query",
    ):
        if key in data:
            return key
    return "other"


def dedup_key(prefix: str, update_id: int) -> str:
    return f"{prefix}tgupd:{int(update_id)}"

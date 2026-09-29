"""Cookies, CSRF, client IP, and redaction helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from starlette.requests import Request

SESSION_COOKIE = "wa_session"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(secret: str, token: str) -> str:
    return hmac.new(secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def csrf_matches(expected: str, provided: str | None) -> bool:
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected, provided)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()[:80]
    if request.client is None:
        return ""
    return request.client.host


def ip_allowed(allowlist: set[str], ip: str) -> bool:
    if not allowlist:
        return True
    return ip in allowlist


def redact_webhook_url(url: str) -> str:
    """Hide the secret path segment. The public host stays visible."""
    if "/tg/webhook/" not in url:
        return url
    base, _, _rest = url.partition("/tg/webhook/")
    return f"{base}/tg/webhook/***"


def public_config(settings: Any) -> dict[str, Any]:
    """Values the login page may see. No tokens, DSNs, or session secrets."""
    return {
        "enabled": bool(settings.web_admin_enabled),
        "bot_username": settings.telegram_login_bot_username or "",
        "panel_url": settings.admin_panel_url or "",
    }

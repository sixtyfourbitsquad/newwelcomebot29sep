"""Tests for web admin auth, inbox routing, and webhook guards."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from telegram.constants import ChatType
from telegram.error import RetryAfter

from configs.settings import Settings
from handlers.registration import register_handlers
from services.live_chat_service import (
    LiveChatService,
    classify_message,
    union_admin_ids,
    user_id_from_forward_origin,
)
from services.webhook_guard import dedup_key, webhook_secret_ok
from utils import flood
from webadmin.permissions import resolve_panel_access, role_allows
from webadmin.security import csrf_matches, redact_webhook_url, token_digest
from webadmin.telegram_auth import (
    TelegramAuthError,
    build_data_check_string,
    telegram_login_hash,
    verify_telegram_login,
)
from telegram.ext import Application

TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd"


def settings_for(**overrides) -> Settings:
    data = dict(
        bot_token=TOKEN,
        webhook_base_url="https://bot.example.com",
        webhook_secret="s" * 24,
        admin_user_ids_csv="111",
        web_admin_enabled=True,
        admin_panel_url="https://admin.example.com",
        telegram_login_bot_username="my_bot",
        web_session_secret="x" * 40,
        web_cookie_secure=False,
    )
    data.update(overrides)
    return Settings(**data)


def signed(user_id: int, auth_date: int | None = None, **extra) -> dict:
    data = {
        "auth_date": int(time.time()) if auth_date is None else auth_date,
        "first_name": "Ada",
        "id": user_id,
        "username": "ada",
    }
    data.update(extra)
    payload = dict(data)
    payload["hash"] = telegram_login_hash(build_data_check_string(data), TOKEN)
    return payload


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, object] = {}

    def pipeline(self):
        return _Pipe(self.store)

    async def expire(self, key, seconds):
        return True

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)
        return 1


class _Pipe:
    def __init__(self, store) -> None:
        self.store = store
        self.ops = []

    def incr(self, key):
        self.ops.append(("incr", key))
        return self

    def ttl(self, key):
        self.ops.append(("ttl", key))
        return self

    async def execute(self):
        out = []
        for op, key in self.ops:
            if op == "incr":
                self.store[key] = int(self.store.get(key, 0)) + 1
                out.append(self.store[key])
            else:
                out.append(-1)
        return out


class FakeSessions:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.rows: dict[str, dict] = {}

    async def create(self, *, token, admin_id, role, csrf_token, ttl_hours, ip, user_agent):
        self.rows[token_digest(self.secret, token)] = {
            "admin_id": admin_id,
            "role": role,
            "csrf_token": csrf_token,
        }

    async def get_valid(self, token):
        if not token:
            return None
        return self.rows.get(token_digest(self.secret, token))

    async def revoke(self, token):
        self.rows.pop(token_digest(self.secret, token), None)


class FakeAdmins:
    def __init__(self, rows: dict[int, dict] | None = None) -> None:
        self.rows = rows or {}

    async def get_admin(self, admin_id: int):
        return self.rows.get(int(admin_id))

    async def touch_login(self, admin_id: int):
        return None

    async def is_active_admin(self, admin_id: int) -> bool:
        row = self.rows.get(int(admin_id))
        return bool(row and row.get("is_active", True))

    async def list_active_ids(self) -> list[int]:
        return [int(k) for k, row in self.rows.items() if row.get("is_active", True)]


class FakeAudit:
    def __init__(self) -> None:
        self.events = []

    async def write(self, **kwargs):
        self.events.append(kwargs)


def panel_app(admins: FakeAdmins, *, owner_pin: str | None = None) -> tuple[TestClient, Settings, FakeAudit]:
    from webadmin.router import router

    cfg = settings_for(web_owner_pin=owner_pin)
    audit = FakeAudit()
    app = FastAPI()
    app.include_router(router)
    app.state.settings = cfg
    app.state.redis = FakeRedis()
    app.state.web = SimpleNamespace(
        sessions=FakeSessions(cfg.web_session_secret.get_secret_value()),
        audit=audit,
        admins=admins,
        cache=SimpleNamespace(invalidate=AsyncMock()),
    )
    return TestClient(app), cfg, audit


def test_valid_telegram_login_hash():
    payload = signed(111)
    identity = verify_telegram_login(payload, TOKEN, max_age_seconds=86400)
    assert identity["id"] == 111
    assert identity["username"] == "ada"


def test_invalid_telegram_hash():
    payload = signed(111)
    payload["hash"] = "0" * 64
    with pytest.raises(TelegramAuthError, match="invalid hash"):
        verify_telegram_login(payload, TOKEN, max_age_seconds=86400)


def test_expired_telegram_auth():
    payload = signed(111, auth_date=int(time.time()) - 90000)
    with pytest.raises(TelegramAuthError, match="expired"):
        verify_telegram_login(payload, TOKEN, max_age_seconds=86400, now=int(time.time()))


def test_access_rules():
    assert resolve_panel_access(5, [5], None) == (True, "owner")
    assert resolve_panel_access(9, [5], None) == (False, "")
    assert resolve_panel_access(9, [], {"role": "admin", "is_active": True}) == (True, "admin")
    assert resolve_panel_access(9, [], {"role": "support", "is_active": True}) == (True, "admin")
    assert resolve_panel_access(9, [], {"role": "owner", "is_active": False}) == (False, "")
    assert resolve_panel_access(5, [5], {"role": "admin", "is_active": False}) == (True, "admin")
    assert role_allows("owner", "admins.manage")
    assert not role_allows("admin", "admins.manage")
    assert role_allows("admin", "inbox")
    assert role_allows("admin", "broadcast")
    assert not role_allows("stranger", "dashboard")


def test_login_logout_and_csrf():
    client, _cfg, audit = panel_app(FakeAdmins())
    response = client.post("/panel/api/login", json=signed(111))
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "owner"
    assert "wa_session" in response.headers["set-cookie"]
    assert "httponly" in response.headers["set-cookie"].lower()
    denied = client.post("/panel/api/logout")
    assert denied.status_code == 403
    ok = client.post("/panel/api/logout", headers={"X-CSRF-Token": body["csrf_token"]})
    assert ok.status_code == 200
    assert client.get("/panel/api/me").status_code == 401
    assert any(event["action"] == "login" for event in audit.events)
    assert any(event["action"] == "logout" for event in audit.events)


def test_non_admin_rejected_and_active_admin_allowed():
    admins = FakeAdmins({222: {"admin_id": 222, "role": "admin", "is_active": True}})
    client, _cfg, audit = panel_app(admins)
    rejected = client.post("/panel/api/login", json=signed(333))
    assert rejected.status_code == 403
    assert any(event["action"] == "access_denied" for event in audit.events)
    allowed = client.post("/panel/api/login", json=signed(222))
    assert allowed.status_code == 200
    assert allowed.json()["role"] == "admin"
    assert client.get("/panel/api/audit").status_code == 403
    assert client.get("/panel/api/me").status_code == 200


def test_invalid_login_is_audited():
    client, _cfg, audit = panel_app(FakeAdmins())
    payload = signed(111)
    payload["hash"] = "abc"
    response = client.post("/panel/api/login", json=payload)
    assert response.status_code == 401
    assert audit.events[-1]["action"] == "login_failed"
    assert "BOT_TOKEN" not in response.text
    assert TOKEN not in response.text


def test_webhook_secret_and_duplicate_key():
    assert webhook_secret_ok(expected="abc", provided="abc", required=False)
    assert not webhook_secret_ok(expected="abc", provided="nope", required=False)
    assert not webhook_secret_ok(expected="abc", provided=None, required=False)
    assert webhook_secret_ok(expected=None, provided=None, required=False)
    assert not webhook_secret_ok(expected=None, provided=None, required=True)
    assert dedup_key("rate:", 9) == "rate:tgupd:9"
    assert redact_webhook_url("https://bot.example.com/tg/webhook/secret").endswith("/tg/webhook/***")
    assert csrf_matches("token", "token")
    assert not csrf_matches("token", "other")


@pytest.mark.asyncio
async def test_duplicate_update_claim():
    redis = FakeRedis()
    key = dedup_key("rate:", 42)
    assert await redis.set(key, "1", nx=True, ex=10) is True
    assert await redis.set(key, "1", nx=True, ex=10) is None


def test_union_admin_ids_is_shared():
    assert union_admin_ids([10, 11], [11, 20]) == [10, 11, 20]


@pytest.mark.asyncio
async def test_forward_reaches_every_admin():
    admins = FakeAdmins(
        {
            20: {"role": "admin", "is_active": True},
            30: {"role": "owner", "is_active": True},
        }
    )
    service = LiveChatService(
        admin_user_ids=[10],
        users=SimpleNamespace(),
        redis=FakeRedis(),
        rate=SimpleNamespace(allow=AsyncMock(return_value=True)),
        admins=admins,
    )
    bot = SimpleNamespace(
        forward_message=AsyncMock(return_value=SimpleNamespace(message_id=7)),
    )
    message = _user_message(text="hello")
    await service.forward_user_message(bot, message)
    targets = sorted(call.kwargs["chat_id"] for call in bot.forward_message.await_args_list)
    assert targets == [10, 20, 30]


@pytest.mark.asyncio
async def test_reply_maps_to_original_user_only():
    service = _service()
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=3)))
    message = _admin_reply(text="answer", target_user_id=55)
    assert await service.relay_admin_reply(bot, message) is True
    assert bot.send_message.await_args.kwargs["chat_id"] == 55
    assert bot.send_message.await_args.kwargs["text"] == "answer"


@pytest.mark.asyncio
async def test_plain_admin_message_is_not_sent():
    service = _service()
    bot = SimpleNamespace(send_message=AsyncMock())
    message = _admin_reply(text="not a reply", target_user_id=None)
    message.reply_to_message = None
    assert await service.relay_admin_reply(bot, message) is False
    bot.send_message.assert_not_awaited()


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("text", "text"),
        ("photo", "photo"),
        ("video", "video"),
        ("voice", "voice"),
        ("document", "document"),
        ("sticker", "sticker"),
    ],
)
def test_message_types(field, expected):
    message = _media(field)
    kind, _preview, file_id = classify_message(message)
    assert kind == expected
    if expected == "text":
        assert file_id is None
    else:
        assert file_id


def test_forward_origin_user():
    origin = SimpleNamespace(sender_user=SimpleNamespace(id=77))
    assert user_id_from_forward_origin(origin) == 77
    assert user_id_from_forward_origin(None) is None


@pytest.mark.asyncio
async def test_flood_wait_retries(monkeypatch):
    sleeps = []

    async def _sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(flood.asyncio, "sleep", _sleep)
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RetryAfter(0)
        return "sent"

    assert await flood.with_flood_wait(factory) == "sent"
    assert calls["n"] == 2
    assert sleeps


def test_existing_commands_stay_registered():
    application = (
        Application.builder()
        .token("123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        .build()
    )
    register_handlers(application, settings_for())
    names = set()
    for group in application.handlers.values():
        for handler in group:
            callback = getattr(handler, "callback", None)
            if callback is not None:
                names.add(callback.__name__)
    for required in (
        "cmd_start",
        "cmd_help",
        "cmd_admin",
        "cmd_cancel",
        "cmd_wizard_skip",
        "cmd_done_router",
        "admin_inbox_reply",
    ):
        assert required in names


def test_session_expiry_is_enforced_in_query():
    import inspect

    from database.repositories.session_repo import SessionRepository

    source = inspect.getsource(SessionRepository.get_valid)
    assert "expires_at > now()" in source
    assert "revoked_at IS NULL" in source


def _service() -> LiveChatService:
    return LiveChatService(
        admin_user_ids=[10],
        users=SimpleNamespace(set_broadcast_status=AsyncMock()),
        redis=FakeRedis(),
        rate=SimpleNamespace(allow=AsyncMock(return_value=True)),
    )


def _user_message(*, text: str):
    return SimpleNamespace(
        chat=SimpleNamespace(type=ChatType.PRIVATE),
        chat_id=99,
        from_user=SimpleNamespace(id=99),
        message_id=4,
        text=text,
        caption=None,
        photo=None,
        video=None,
        voice=None,
        audio=None,
        document=None,
        animation=None,
        sticker=None,
    )


def _admin_reply(*, text: str, target_user_id: int | None):
    origin = None
    if target_user_id is not None:
        origin = SimpleNamespace(sender_user=SimpleNamespace(id=target_user_id))
    return SimpleNamespace(
        chat=SimpleNamespace(type=ChatType.PRIVATE),
        chat_id=10,
        reply_to_message=SimpleNamespace(forward_origin=origin, message_id=8),
        from_user=SimpleNamespace(id=10),
        message_id=12,
        text=text,
        caption=None,
        photo=None,
        video=None,
        voice=None,
        audio=None,
        document=None,
        animation=None,
        sticker=None,
    )


def _media(field: str):
    base = dict(
        text=None,
        caption="cap",
        photo=None,
        video=None,
        voice=None,
        audio=None,
        document=None,
        animation=None,
        sticker=None,
    )
    if field == "text":
        base["text"] = "hello"
    elif field == "photo":
        base["photo"] = [SimpleNamespace(file_id="photo-file")]
    elif field == "video":
        base["video"] = SimpleNamespace(file_id="video-file")
    elif field == "voice":
        base["voice"] = SimpleNamespace(file_id="voice-file")
    elif field == "document":
        base["document"] = SimpleNamespace(file_id="doc-file", file_name="a.pdf")
    elif field == "sticker":
        base["sticker"] = SimpleNamespace(file_id="sticker-file", emoji="★")
    return SimpleNamespace(**base)

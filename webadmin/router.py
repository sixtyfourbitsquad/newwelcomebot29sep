"""Web admin HTTP API and panel pages."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from models.domain import AdminRole, BroadcastStatus
from services.metrics import METRICS
from services.outbound_sender import send_from_payload
from services.rate_limit_service import RateLimitService
from utils.datetime_parse import parse_iso_utc
from utils.flood import with_flood_wait
from webadmin.jsonutil import to_jsonable
from webadmin.payloads import message_payload
from webadmin.permissions import resolve_panel_access, role_allows
from webadmin.security import SESSION_COOKIE, client_ip, csrf_matches, ip_allowed, new_token, public_config, redact_webhook_url
from webadmin.telegram_auth import TelegramAuthError, verify_telegram_login

logger = logging.getLogger(__name__)
router = APIRouter()
_STATIC = Path(__file__).resolve().parent / "static"
_LOGIN_FIELDS = {"id", "first_name", "last_name", "username", "photo_url", "auth_date", "hash"}


@dataclass(slots=True)
class Principal:
    admin_id: int
    role: str
    csrf: str


def _settings(request: Request):
    return request.app.state.settings


def _web(request: Request):
    return request.app.state.web


def _repos(request: Request) -> dict:
    return request.app.state.ptb.bot_data["repos"]


def _services(request: Request) -> dict:
    return request.app.state.ptb.bot_data["services"]


async def _audit(request: Request, admin_id: int | None, action: str, success: bool, detail: dict | None = None) -> None:
    try:
        await _web(request).audit.write(
            admin_id=admin_id,
            action=action,
            success=success,
            ip=client_ip(request),
            detail=detail or {},
        )
    except Exception:
        logger.exception("audit write failed action=%s", action)


async def _limited(request: Request, bucket: str, limit: int) -> bool:
    try:
        rate = RateLimitService(request.app.state.redis, prefix=f"{_settings(request).redis_rate_prefix}web:")
        allowed = await rate.allow(bucket, limit=limit, window_seconds=60)
        return not allowed
    except Exception:
        logger.exception("web rate limit check failed")
        return False


async def _access(request: Request, telegram_id: int) -> tuple[bool, str]:
    row = None
    try:
        row = await _web(request).admins.get_admin(int(telegram_id))
    except Exception:
        logger.exception("admin lookup failed")
    return resolve_panel_access(int(telegram_id), _settings(request).admin_user_ids, row)


async def guard(request: Request, permission: str, *, mutating: bool = False) -> Principal:
    settings = _settings(request)
    if not settings.web_admin_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    ip = client_ip(request)
    if not ip_allowed(settings.ip_allowlist(), ip):
        await _audit(request, None, "access_denied", False, {"reason": "ip"})
        raise HTTPException(status_code=403, detail="Forbidden")
    token = request.cookies.get(SESSION_COOKIE) or ""
    try:
        row = await _web(request).sessions.get_valid(token)
    except Exception:
        logger.exception("session lookup failed")
        raise HTTPException(status_code=503, detail="Admin session store is unavailable") from None
    if row is None:
        raise HTTPException(status_code=401, detail="Login required")
    admin_id = int(row["admin_id"])
    allowed, role = await _access(request, admin_id)
    if not allowed:
        await _audit(request, admin_id, "access_denied", False, {"reason": "inactive"})
        raise HTTPException(status_code=403, detail="Forbidden")
    if not role_allows(role, permission):
        await _audit(request, admin_id, "access_denied", False, {"permission": permission})
        raise HTTPException(status_code=403, detail="Forbidden")
    if mutating and not csrf_matches(str(row["csrf_token"]), request.headers.get("x-csrf-token")):
        METRICS.inc("web_csrf_rejected_total")
        raise HTTPException(status_code=403, detail="CSRF check failed")
    if await _limited(request, f"api:{admin_id}", 180):
        raise HTTPException(status_code=429, detail="Too many requests")
    return Principal(admin_id=admin_id, role=role, csrf=str(row["csrf_token"]))


def _cookie(response: JSONResponse, request: Request, token: str) -> None:
    settings = _settings(request)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=settings.web_cookie_secure,
        samesite=settings.web_cookie_samesite,
        max_age=int(settings.web_session_expire_hours) * 3600,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


async def _issue_session(request: Request, admin_id: int, role: str) -> JSONResponse:
    token = new_token()
    csrf = new_token()
    await _web(request).sessions.create(
        token=token,
        admin_id=admin_id,
        role=role,
        csrf_token=csrf,
        ttl_hours=int(_settings(request).web_session_expire_hours),
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent") or "",
    )
    try:
        await _web(request).admins.touch_login(admin_id)
    except Exception:
        logger.exception("last login update failed")
    await _audit(request, admin_id, "login", True, {"role": role})
    METRICS.inc("web_login_total")
    response = JSONResponse({"ok": True, "admin_id": admin_id, "role": role, "csrf_token": csrf})
    _cookie(response, request, token)
    return response


def _panel_html() -> str:
    return (_STATIC / "panel.html").read_text(encoding="utf-8")


@router.get("/", include_in_schema=False)
@router.get("/admin", include_in_schema=False)
@router.get("/admin/", include_in_schema=False)
@router.get("/panel", include_in_schema=False)
@router.get("/panel/", include_in_schema=False)
async def panel_page(request: Request) -> Response:
    if not _settings(request).web_admin_enabled:
        return JSONResponse({"service": "telegram-community-bot"})
    return HTMLResponse(_panel_html(), headers={"Cache-Control": "no-store"})


@router.get("/panel/static/{name}", include_in_schema=False)
async def panel_static(name: str) -> FileResponse:
    if name not in {"panel.js", "panel.css"}:
        raise HTTPException(status_code=404, detail="Not found")
    path = _STATIC / name
    media = "text/javascript" if name.endswith(".js") else "text/css"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "no-store"})


@router.get("/panel/api/public-config")
async def public_panel_config(request: Request) -> dict:
    return public_config(_settings(request))


@router.post("/panel/api/login")
async def login(request: Request) -> JSONResponse:
    settings = _settings(request)
    if not settings.web_admin_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    ip = client_ip(request)
    if not ip_allowed(settings.ip_allowlist(), ip):
        await _audit(request, None, "access_denied", False, {"reason": "ip"})
        raise HTTPException(status_code=403, detail="Forbidden")
    if await _limited(request, f"login:{ip}", 10):
        raise HTTPException(status_code=429, detail="Too many requests")
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid login payload")
    data = {key: body[key] for key in body if key in _LOGIN_FIELDS}
    try:
        identity = verify_telegram_login(
            data,
            settings.bot_token.get_secret_value(),
            max_age_seconds=settings.telegram_auth_max_age_seconds,
        )
    except TelegramAuthError as exc:
        await _audit(request, None, "login_failed", False, {"reason": str(exc)})
        METRICS.inc("web_login_failed_total")
        raise HTTPException(status_code=401, detail="Telegram login rejected") from None
    allowed, role = await _access(request, int(identity["id"]))
    if not allowed:
        await _audit(request, int(identity["id"]), "access_denied", False, {"reason": "not_admin"})
        raise HTTPException(status_code=403, detail="Forbidden")
    pin = settings.web_owner_pin.get_secret_value() if settings.web_owner_pin else ""
    if role == "owner" and pin:
        mfa = new_token()
        await request.app.state.redis.set(
            f"webmfa:{mfa}",
            json.dumps({"admin_id": int(identity["id"]), "role": role}),
            ex=300,
        )
        return JSONResponse({"ok": True, "mfa_required": True, "mfa_token": mfa})
    return await _issue_session(request, int(identity["id"]), role)


@router.post("/panel/api/login/pin")
async def login_pin(request: Request) -> JSONResponse:
    settings = _settings(request)
    if not settings.web_admin_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    if await _limited(request, f"pin:{client_ip(request)}", 8):
        raise HTTPException(status_code=429, detail="Too many requests")
    body = await request.json()
    mfa = str(body.get("mfa_token") or "")
    pin = str(body.get("pin") or "")
    expected = settings.web_owner_pin.get_secret_value() if settings.web_owner_pin else ""
    raw = await request.app.state.redis.get(f"webmfa:{mfa}")
    if not raw or not expected or not csrf_matches(expected, pin):
        await _audit(request, None, "login_failed", False, {"reason": "pin"})
        raise HTTPException(status_code=401, detail="Telegram login rejected")
    text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    saved = json.loads(text)
    await request.app.state.redis.delete(f"webmfa:{mfa}")
    return await _issue_session(request, int(saved["admin_id"]), str(saved["role"]))


@router.post("/panel/api/logout")
async def logout(request: Request) -> JSONResponse:
    principal = await guard(request, "dashboard", mutating=True)
    token = request.cookies.get(SESSION_COOKIE) or ""
    await _web(request).sessions.revoke(token)
    await _audit(request, principal.admin_id, "logout", True, {})
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/panel/api/me")
async def me(request: Request) -> dict:
    principal = await guard(request, "dashboard")
    return {"admin_id": principal.admin_id, "role": principal.role, "csrf_token": principal.csrf}


@router.get("/panel/api/dashboard")
async def dashboard(request: Request) -> dict:
    await guard(request, "dashboard")
    repos = _repos(request)
    users = await repos["users"].get_stats_snapshot()
    windows = await repos["users"].get_activity_windows()
    started = await repos["users"].count_started()
    onboarding = await repos["onboarding"].counts()
    inbox = await repos["inbox"].counts()
    redis = request.app.state.redis
    queue = await redis.llen(_settings(request).redis_broadcast_queue)
    recent = await repos["broadcasts"].list_recent(limit=5)
    pg_ok, redis_ok = await _deps_ok(request)
    reg = getattr(request.app.state, "webhook_registered", None)
    started_at = float(request.app.state.ptb.bot_data.get("process_started_at") or time.time())
    workers = await redis.hgetall("workers:status")
    return to_jsonable(
        {
            "users": users,
            "activity": windows,
            "started_users": started,
            "onboarding": onboarding,
            "inbox": inbox,
            "broadcast_queue": int(queue or 0),
            "recent_broadcasts": recent,
            "postgres_ok": pg_ok,
            "redis_ok": redis_ok,
            "webhook": _webhook_label(reg),
            "workers": workers,
            "uptime_seconds": int(time.time() - started_at),
        }
    )


@router.get("/panel/api/inbox")
async def inbox_list(request: Request) -> dict:
    await guard(request, "inbox")
    rows = await _repos(request)["inbox"].list_conversations(limit=80)
    return {
        "shared": True,
        "notice": "Every admin sees every message. A reply goes only to the user you select.",
        "conversations": to_jsonable(rows),
    }


@router.get("/panel/api/inbox/{user_id}")
async def inbox_thread(user_id: int, request: Request) -> dict:
    await guard(request, "inbox")
    thread = await _repos(request)["inbox"].list_thread(user_id)
    user = await _repos(request)["users"].get_user(user_id)
    return {"user": to_jsonable(user), "thread": to_jsonable(thread)}


@router.post("/panel/api/inbox/{user_id}/reply")
async def inbox_reply(user_id: int, request: Request) -> dict:
    principal = await guard(request, "inbox", mutating=True)
    body = await request.json()
    if body.get("confirm") is not True or int(body.get("user_id") or 0) != int(user_id):
        raise HTTPException(status_code=400, detail="Confirm the selected user before sending")
    kind = str(body.get("kind") or "text")
    try:
        payload = message_payload(
            kind=kind,
            text=str(body.get("text") or ""),
            caption=str(body.get("caption") or ""),
            file_id=str(body.get("file_id") or ""),
            keyboard=body.get("inline_keyboard"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _send_user_reply(request, principal.admin_id, user_id, payload)


@router.post("/panel/api/inbox/replies/{reply_id}/retry")
async def retry_reply(reply_id: int, request: Request) -> dict:
    principal = await guard(request, "inbox", mutating=True)
    body = await request.json()
    if body.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="Confirmation required")
    row = await _repos(request)["inbox"].get_reply(reply_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Reply not found")
    payload = row.get("payload") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not payload:
        raise HTTPException(status_code=400, detail="This reply has no stored payload to retry")
    result = await _deliver(request, int(row["target_user_id"]), dict(payload))
    await _repos(request)["inbox"].mark_reply(
        reply_id,
        status="delivered" if result["ok"] else "failed",
        error=result.get("error"),
        sent_message_id=result.get("message_id"),
    )
    await _audit(request, principal.admin_id, "inbox_retry", bool(result["ok"]), {"reply_id": reply_id})
    return result


@router.get("/panel/api/broadcasts")
async def list_broadcasts(request: Request) -> dict:
    await guard(request, "broadcast")
    repos = _repos(request)
    return to_jsonable(
        {
            "recipients": await repos["users"].count_active_recipients(),
            "active": await repos["broadcasts"].list_active(),
            "recent": await repos["broadcasts"].list_recent(limit=30),
        }
    )


@router.post("/panel/api/broadcasts")
async def create_broadcast(request: Request) -> dict:
    principal = await guard(request, "broadcast", mutating=True)
    body = await request.json()
    try:
        payload = message_payload(
            kind=str(body.get("kind") or "text"),
            text=str(body.get("text") or ""),
            caption=str(body.get("caption") or ""),
            file_id=str(body.get("file_id") or ""),
            keyboard=body.get("inline_keyboard"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    bid = await _repos(request)["broadcasts"].create_broadcast(
        created_by=principal.admin_id,
        payload=payload,
        status=BroadcastStatus.DRAFT,
    )
    jobs = 0
    if body.get("start") is True:
        jobs = await _services(request)["broadcast"].enqueue_broadcast(bid)
    await _audit(request, principal.admin_id, "broadcast_create", True, {"broadcast_id": bid, "started": bool(body.get("start"))})
    return {"id": bid, "jobs": jobs}


@router.post("/panel/api/broadcasts/{broadcast_id}/test")
async def test_broadcast(broadcast_id: int, request: Request) -> dict:
    principal = await guard(request, "broadcast", mutating=True)
    payload = await _repos(request)["broadcasts"].get_payload(broadcast_id)
    result = await _deliver(request, principal.admin_id, payload)
    return result


@router.post("/panel/api/broadcasts/{broadcast_id}/{action}")
async def control_broadcast(broadcast_id: int, action: str, request: Request) -> dict:
    principal = await guard(request, "broadcast", mutating=True)
    service = _services(request)["broadcast"]
    if action == "pause":
        await service.set_paused(broadcast_id, True)
    elif action == "resume":
        await service.set_paused(broadcast_id, False)
    elif action == "stop":
        body = await request.json()
        if body.get("confirm") is not True:
            raise HTTPException(status_code=400, detail="Confirmation required")
        await service.cancel(broadcast_id)
    elif action == "start":
        jobs = await service.enqueue_broadcast(broadcast_id)
        await _audit(request, principal.admin_id, "broadcast_start", True, {"broadcast_id": broadcast_id})
        return {"ok": True, "jobs": jobs}
    else:
        raise HTTPException(status_code=404, detail="Unknown action")
    await _audit(request, principal.admin_id, f"broadcast_{action}", True, {"broadcast_id": broadcast_id})
    return {"ok": True}


@router.get("/panel/api/scheduled")
async def list_scheduled(request: Request) -> dict:
    await guard(request, "broadcast")
    repo = _repos(request)["scheduled"]
    return to_jsonable(
        {
            "timezone": "UTC",
            "upcoming": await repo.list_upcoming(limit=50),
            "recent": await repo.list_recent(limit=50),
        }
    )


@router.post("/panel/api/scheduled")
async def create_scheduled(request: Request) -> dict:
    principal = await guard(request, "broadcast", mutating=True)
    body = await request.json()
    try:
        run_at = parse_iso_utc(str(body.get("run_at") or ""))
        payload = message_payload(
            kind=str(body.get("kind") or "text"),
            text=str(body.get("text") or ""),
            caption=str(body.get("caption") or ""),
            file_id=str(body.get("file_id") or ""),
            keyboard=body.get("inline_keyboard"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job_id = await _repos(request)["scheduled"].create_job(
        created_by=principal.admin_id,
        run_at=run_at,
        payload={"mode": "broadcast_enqueue", "message": payload, "created_by": principal.admin_id},
    )
    await _audit(request, principal.admin_id, "schedule_create", True, {"job_id": job_id})
    return {"id": job_id, "run_at": run_at.isoformat(), "timezone": "UTC"}


@router.patch("/panel/api/scheduled/{job_id}")
async def edit_scheduled(job_id: int, request: Request) -> dict:
    principal = await guard(request, "broadcast", mutating=True)
    body = await request.json()
    run_at = parse_iso_utc(str(body["run_at"])) if body.get("run_at") else None
    updated = await _repos(request)["scheduled"].update_pending(job_id, run_at=run_at)
    if not updated:
        raise HTTPException(status_code=404, detail="Pending job not found")
    await _audit(request, principal.admin_id, "schedule_edit", True, {"job_id": job_id})
    return {"ok": True}


@router.post("/panel/api/scheduled/{job_id}/cancel")
async def cancel_scheduled(job_id: int, request: Request) -> dict:
    principal = await guard(request, "broadcast", mutating=True)
    body = await request.json()
    if body.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="Confirmation required")
    await _repos(request)["scheduled"].cancel(job_id)
    await _audit(request, principal.admin_id, "schedule_cancel", True, {"job_id": job_id})
    return {"ok": True}


@router.get("/panel/api/welcome")
async def welcome_get(request: Request) -> dict:
    await guard(request, "welcome")
    repo = _repos(request)["settings"]
    channel = await repo.get_channel_settings()
    return to_jsonable({"enabled": channel.get("welcome_enabled", True), "steps": await repo.list_welcome_steps()})


@router.post("/panel/api/welcome")
async def welcome_save(request: Request) -> dict:
    principal = await guard(request, "welcome", mutating=True)
    body = await request.json()
    repo = _repos(request)["settings"]
    if "enabled" in body:
        await repo.set_welcome_enabled(bool(body["enabled"]))
    if body.get("text") or body.get("file_id"):
        step = int(body["step_order"]) if body.get("step_order") else await repo.next_step_order("welcome_messages")
        payload = message_payload(
            kind=str(body.get("kind") or "text"),
            text=str(body.get("text") or ""),
            caption=str(body.get("caption") or ""),
            file_id=str(body.get("file_id") or ""),
            keyboard=body.get("inline_keyboard"),
        )
        await repo.upsert_welcome_step(step, payload)
    if body.get("swap"):
        pair = body["swap"]
        await repo.swap_step_order("welcome_messages", int(pair[0]), int(pair[1]))
    await _web(request).cache.invalidate("welcome")
    await _audit(request, principal.admin_id, "welcome_update", True, {})
    return {"ok": True}


@router.delete("/panel/api/welcome/{step_order}")
async def welcome_delete(step_order: int, request: Request) -> dict:
    principal = await guard(request, "welcome", mutating=True)
    body = await request.json()
    if body.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="Confirmation required")
    await _repos(request)["settings"].delete_welcome_step(step_order)
    await _audit(request, principal.admin_id, "welcome_delete", True, {"step": step_order})
    return {"ok": True}


@router.get("/panel/api/onboarding")
async def onboarding_get(request: Request) -> dict:
    await guard(request, "onboarding")
    channel = await _repos(request)["settings"].get_channel_settings()
    return to_jsonable(
        {
            "env_enabled": _settings(request).onboarding_drip_enabled,
            "enabled": bool(channel.get("onboarding_enabled", True)) and _settings(request).onboarding_drip_enabled,
            "steps": await _repos(request)["onboarding"].list_messages(),
            "jobs": await _repos(request)["onboarding"].list_recent_jobs(limit=30),
            "counts": await _repos(request)["onboarding"].counts(),
        }
    )


@router.post("/panel/api/onboarding")
async def onboarding_save(request: Request) -> dict:
    principal = await guard(request, "onboarding", mutating=True)
    body = await request.json()
    if "enabled" in body:
        await _repos(request)["settings"].set_onboarding_enabled(bool(body["enabled"]))
    if body.get("step_order"):
        payload = message_payload(
            kind=str(body.get("kind") or "text"),
            text=str(body.get("text") or ""),
            caption=str(body.get("caption") or ""),
            file_id=str(body.get("file_id") or ""),
        )
        delay = int(body.get("delay_seconds") or 0)
        await _repos(request)["onboarding"].upsert_message(int(body["step_order"]), delay, payload)
    await _audit(request, principal.admin_id, "onboarding_update", True, {})
    return {"ok": True}


@router.get("/panel/api/retention")
async def retention_get(request: Request) -> dict:
    await guard(request, "retention")
    repo = _repos(request)["settings"]
    channel = await repo.get_channel_settings()
    return to_jsonable(
        {
            "enabled": bool(channel.get("retention_enabled", True)),
            "steps": await repo.list_retention_steps(),
        }
    )


@router.post("/panel/api/retention")
async def retention_save(request: Request) -> dict:
    principal = await guard(request, "retention", mutating=True)
    body = await request.json()
    repo = _repos(request)["settings"]
    if "enabled" in body:
        await repo.set_retention_enabled(bool(body["enabled"]))
    if body.get("text") or body.get("file_id"):
        step = int(body["step_order"]) if body.get("step_order") else await repo.next_step_order("retention_messages")
        payload = message_payload(
            kind=str(body.get("kind") or "text"),
            text=str(body.get("text") or ""),
            caption=str(body.get("caption") or ""),
            file_id=str(body.get("file_id") or ""),
        )
        delay = int(body.get("delay_seconds") or 0)
        await repo.upsert_retention_step(step, delay, payload)
    if body.get("swap"):
        pair = body["swap"]
        await repo.swap_step_order("retention_messages", int(pair[0]), int(pair[1]))
    await _audit(request, principal.admin_id, "retention_update", True, {})
    return {"ok": True}


@router.delete("/panel/api/retention/{step_order}")
async def retention_delete(step_order: int, request: Request) -> dict:
    principal = await guard(request, "retention", mutating=True)
    body = await request.json()
    if body.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="Confirmation required")
    await _repos(request)["settings"].delete_retention_step(step_order)
    await _audit(request, principal.admin_id, "retention_delete", True, {"step": step_order})
    return {"ok": True}


@router.get("/panel/api/channel")
async def channel_get(request: Request) -> dict:
    await guard(request, "dashboard")
    repo = _repos(request)["settings"]
    channel = await repo.get_channel_settings()
    live = await repo.get_livestream_settings()
    permissions = None
    chat_id = channel.get("monitored_chat_id")
    if chat_id:
        try:
            bot = request.app.state.ptb.bot
            me = await bot.get_me()
            member = await bot.get_chat_member(int(chat_id), me.id)
            permissions = str(getattr(member, "status", ""))
        except Exception as exc:
            permissions = f"unavailable: {exc.__class__.__name__}"
    return to_jsonable({"channel": channel, "livestream": live, "bot_status": permissions})


@router.post("/panel/api/channel")
async def channel_save(request: Request) -> dict:
    principal = await guard(request, "channel.write", mutating=True)
    body = await request.json()
    repo = _repos(request)["settings"]
    if "monitored_chat_id" in body:
        raw = body.get("monitored_chat_id")
        if raw in (None, ""):
            await repo.set_monitored_chat(None)
        else:
            try:
                await repo.set_monitored_chat(int(raw))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Channel id must be a number") from None
    if "auto_approve_join_requests" in body:
        await repo.set_auto_approve_join_requests(bool(body["auto_approve_join_requests"]))
    if "retention_enabled" in body:
        await repo.set_retention_enabled(bool(body["retention_enabled"]))
    if "welcome_enabled" in body:
        await repo.set_welcome_enabled(bool(body["welcome_enabled"]))
    live_kwargs = {}
    if "notification_template" in body:
        live_kwargs["notification_template"] = str(body["notification_template"])[:1000]
    if "cooldown_seconds" in body:
        live_kwargs["cooldown_seconds"] = max(int(body["cooldown_seconds"]), 0)
    if "manual_live_url" in body:
        live_kwargs["manual_live_url"] = str(body.get("manual_live_url") or "")
    if live_kwargs:
        await repo.update_livestream(**live_kwargs)
    await _web(request).cache.invalidate("channel")
    await _audit(request, principal.admin_id, "channel_update", True, {})
    return {"ok": True}


@router.get("/panel/api/users")
async def users_search(request: Request) -> dict:
    await guard(request, "stats")
    query = request.query_params.get("q") or ""
    rows = await _repos(request)["users"].search(query, limit=50)
    stats = await _repos(request)["users"].get_stats_snapshot()
    return to_jsonable({"stats": stats, "users": rows})


@router.get("/panel/api/users/{user_id}")
async def user_detail(user_id: int, request: Request) -> dict:
    await guard(request, "stats")
    repos = _repos(request)
    return to_jsonable(
        {
            "user": await repos["users"].get_user(user_id),
            "activity": await repos["users"].recent_activity(user_id),
            "thread": await repos["inbox"].list_thread(user_id, limit=30),
        }
    )


@router.get("/panel/api/logs")
async def logs(request: Request) -> dict:
    await guard(request, "logs")
    system = await _repos(request)["settings"].fetch_recent_logs(limit=80)
    return to_jsonable({"system": system})


@router.get("/panel/api/audit")
async def audit_log(request: Request) -> dict:
    await guard(request, "audit.read")
    rows = await _web(request).audit.list_recent(limit=150)
    return to_jsonable({"events": rows})


@router.get("/panel/api/queue")
async def queue(request: Request) -> dict:
    await guard(request, "queue")
    settings = _settings(request)
    redis = request.app.state.redis
    failed = await _repos(request)["inbox"].failed_replies(limit=30)
    scheduled = await _repos(request)["scheduled"].list_recent(limit=20)
    return to_jsonable(
        {
            "broadcast_queue": int(await redis.llen(settings.redis_broadcast_queue) or 0),
            "active_broadcasts": await _repos(request)["broadcasts"].list_active(),
            "failed_replies": failed,
            "scheduled": scheduled,
        }
    )


@router.post("/panel/api/queue/clear")
async def queue_clear(request: Request) -> dict:
    principal = await guard(request, "queue.clear", mutating=True)
    body = await request.json()
    if body.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="Confirmation required")
    key = _settings(request).redis_broadcast_queue
    removed = await request.app.state.redis.delete(key)
    await _audit(request, principal.admin_id, "queue_clear", True, {"removed": int(removed or 0)})
    return {"ok": True}


@router.get("/panel/api/health")
async def health_detail(request: Request) -> dict:
    await guard(request, "health")
    pg_ok, redis_ok = await _deps_ok(request)
    started_at = float(request.app.state.ptb.bot_data.get("process_started_at") or time.time())
    workers = await request.app.state.redis.hgetall("workers:status")
    heartbeats = await request.app.state.redis.hgetall("workers:heartbeat")
    return {
        "postgres_ok": pg_ok,
        "redis_ok": redis_ok,
        "webhook": _webhook_label(getattr(request.app.state, "webhook_registered", None)),
        "workers": workers,
        "worker_heartbeat": heartbeats,
        "uptime_seconds": int(time.time() - started_at),
        "process": _process_stats(),
        "broadcast_queue": int(await request.app.state.redis.llen(_settings(request).redis_broadcast_queue) or 0),
    }


@router.get("/panel/api/setup")
async def setup(request: Request) -> dict:
    await guard(request, "dashboard")
    settings = _settings(request)
    channel = await _repos(request)["settings"].get_channel_settings()
    me = None
    try:
        me = await request.app.state.ptb.bot.get_me()
    except Exception:
        logger.exception("get_me failed")
    return {
        "bot_username": getattr(me, "username", None) or settings.telegram_login_bot_username,
        "uptime_seconds": int(time.time() - float(request.app.state.ptb.bot_data.get("process_started_at") or time.time())),
        "channel_id": channel.get("monitored_chat_id"),
        "join_requests_total": channel.get("join_requests_total", 0),
        "staff_ids": settings.admin_user_ids,
        "webhook_url": redact_webhook_url(settings.webhook_full_url()),
        "webhook_status": _webhook_label(getattr(request.app.state, "webhook_registered", None)),
        "admin_panel_url": settings.admin_panel_url,
    }


@router.get("/panel/api/admins")
async def admins_list(request: Request) -> dict:
    await guard(request, "admins.manage")
    rows = await _web(request).admins.list_panel_admins()
    return to_jsonable({"admins": rows})


@router.post("/panel/api/admins")
async def admins_add(request: Request) -> dict:
    principal = await guard(request, "admins.manage", mutating=True)
    body = await request.json()
    admin_id = int(body["admin_id"])
    role = AdminRole.OWNER if str(body.get("role") or "admin") == "owner" else AdminRole.ADMIN
    await _web(request).admins.add_admin(admin_id, role, principal.admin_id)
    await _audit(request, principal.admin_id, "admin_add", True, {"admin_id": admin_id, "role": role.value})
    return {"ok": True}


@router.post("/panel/api/admins/{admin_id}/role")
async def admins_role(admin_id: int, request: Request) -> dict:
    principal = await guard(request, "admins.manage", mutating=True)
    body = await request.json()
    role = AdminRole.OWNER if str(body.get("role")) == "owner" else AdminRole.ADMIN
    await _web(request).admins.set_role(admin_id, role, principal.admin_id)
    await _audit(request, principal.admin_id, "admin_role", True, {"admin_id": admin_id, "role": role.value})
    return {"ok": True}


@router.post("/panel/api/admins/{admin_id}/active")
async def admins_active(admin_id: int, request: Request) -> dict:
    principal = await guard(request, "admins.manage", mutating=True)
    body = await request.json()
    if body.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="Confirmation required")
    await _web(request).admins.set_active(admin_id, bool(body.get("active")), principal.admin_id)
    await _audit(request, principal.admin_id, "admin_active", True, {"admin_id": admin_id, "active": bool(body.get("active"))})
    return {"ok": True}


@router.delete("/panel/api/admins/{admin_id}")
async def admins_remove(admin_id: int, request: Request) -> dict:
    principal = await guard(request, "admins.manage", mutating=True)
    body = await request.json()
    if body.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="Confirmation required")
    if int(admin_id) == int(principal.admin_id):
        raise HTTPException(status_code=400, detail="You cannot remove your own admin record")
    await _web(request).admins.remove_admin(admin_id)
    await _audit(request, principal.admin_id, "admin_remove", True, {"admin_id": admin_id})
    return {"ok": True}


async def _send_user_reply(request: Request, admin_id: int, user_id: int, payload: dict) -> dict:
    result = await _deliver(request, user_id, payload)
    preview = str(payload.get("text") or payload.get("caption") or payload.get("kind") or "")[:500]
    try:
        await _repos(request)["inbox"].record_reply(
            inbox_message_id=None,
            admin_telegram_id=admin_id,
            target_user_id=user_id,
            reply_message_id=result.get("message_id"),
            source_message_id=None,
            message_type=str(payload.get("kind") or "text"),
            content_text=preview,
            payload=payload,
            delivery_status="delivered" if result["ok"] else "failed",
            telegram_error=result.get("error"),
        )
    except Exception:
        logger.exception("web reply record failed")
    await _audit(request, admin_id, "inbox_reply", bool(result["ok"]), {"user_id": user_id})
    METRICS.inc("inbox_replies_total")
    return result


async def _deliver(request: Request, chat_id: int, payload: dict) -> dict:
    bot = request.app.state.ptb.bot
    try:
        message = await with_flood_wait(lambda: send_from_payload(bot, chat_id=chat_id, payload=payload))
        return {"ok": True, "message_id": getattr(message, "message_id", None)}
    except Exception as exc:
        logger.warning("delivery failed chat=%s error=%s", chat_id, exc.__class__.__name__)
        METRICS.inc("telegram_api_errors_total")
        return {"ok": False, "error": exc.__class__.__name__}


async def _deps_ok(request: Request) -> tuple[bool, bool]:
    pg_ok = False
    redis_ok = False
    try:
        from database.pool import get_pool

        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        pg_ok = True
    except Exception:
        logger.exception("postgres health check failed")
    try:
        await request.app.state.redis.ping()
        redis_ok = True
    except Exception:
        logger.exception("redis health check failed")
    return pg_ok, redis_ok


def _webhook_label(reg: object) -> str:
    if reg is None:
        return "registering"
    if reg is False:
        return "not_registered"
    return "registered"


def _process_stats() -> dict:
    import os
    import sys

    info: dict = {}
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss = float(usage.ru_maxrss)
        if sys.platform == "darwin":
            rss = rss / 1024
        info["memory_kb"] = rss
        info["cpu_user_seconds"] = usage.ru_utime
        info["cpu_system_seconds"] = usage.ru_stime
    except Exception:
        pass
    try:
        info["load_average"] = [round(n, 2) for n in os.getloadavg()]
    except (AttributeError, OSError):
        pass
    return info

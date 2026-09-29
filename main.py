"""
ASGI entrypoint: FastAPI webhook + background asyncio workers.

Run: ``uvicorn main:app --host 0.0.0.0 --port 8000`` from project root.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from telegram import Update
from telegram.error import TelegramError

from bot.application import build_application, create_redis_client, seed_initial_owner
from configs.settings import Settings, get_settings
from database.pool import close_pool, get_pool, init_pool
from database.repositories.admins import AdminRepository
from database.repositories.audit_repo import AuditRepository
from database.repositories.session_repo import SessionRepository
from services.config_cache import ConfigCache
from services.metrics import METRICS
from services.webhook_guard import dedup_key, update_type_name, webhook_secret_ok
from webadmin.router import router
from workers.broadcast_worker import broadcast_worker_loop
from workers.onboarding_worker import onboarding_worker_loop
from workers.retention_worker import retention_worker_loop
from workers.scheduler_worker import scheduler_worker_loop

logger = logging.getLogger(__name__)

_WEBHOOK_RETRY_ATTEMPTS = 6
_WEBHOOK_RETRY_DELAYS_SEC = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)


async def _register_webhook(application, settings: Settings) -> bool:
    """Register webhook with Telegram; retries DNS/transient failures so startup can still succeed."""
    secret = settings.webhook_secret.get_secret_value()
    webhook_route = settings.webhook_path.format(secret=secret)
    url = settings.webhook_full_url()
    kwargs = {
        "url": url,
        # Explicit types so Telegram never drops chat_join_request / chat_member (omit=None varies).
        "allowed_updates": Update.ALL_TYPES,
        "secret_token": settings.telegram_webhook_secret_token.get_secret_value()
        if settings.telegram_webhook_secret_token
        else None,
        "drop_pending_updates": True,
    }
    last_err: BaseException | None = None
    for attempt in range(_WEBHOOK_RETRY_ATTEMPTS):
        try:
            await application.bot.set_webhook(**kwargs)
            logger.info("Webhook set to %s route_suffix=%s", url, webhook_route)
            try:
                whi = await application.bot.get_webhook_info()
                logger.info(
                    "Telegram getWebhookInfo: allowed_updates=%s pending_updates=%s",
                    whi.allowed_updates,
                    whi.pending_update_count,
                )
            except TelegramError as e:
                logger.warning("getWebhookInfo failed after setWebhook: %s", e)
            return True
        except TelegramError as e:
            last_err = e
            logger.warning(
                "setWebhook failed (%s/%s): %s",
                attempt + 1,
                _WEBHOOK_RETRY_ATTEMPTS,
                e,
            )
            if attempt + 1 < _WEBHOOK_RETRY_ATTEMPTS:
                delay = _WEBHOOK_RETRY_DELAYS_SEC[
                    min(attempt, len(_WEBHOOK_RETRY_DELAYS_SEC) - 1)
                ]
                await asyncio.sleep(delay)

    logger.error(
        "Webhook was NOT registered after %s attempts (last error: %s). "
        "Telegram must resolve your hostname (%s). "
        "Check DNS / DuckDNS / nginx HTTPS, then restart this service.",
        _WEBHOOK_RETRY_ATTEMPTS,
        last_err,
        settings.webhook_base_url,
    )
    return False


def _setup_uvloop() -> None:
    """Install uvloop policy when available (Linux/macOS)."""
    try:
        import uvloop

        uvloop.install()
    except Exception:
        logger.warning("uvloop not installed or unsupported; using default loop")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    os.makedirs(settings.storage_dir, exist_ok=True)

    await init_pool(settings)
    pool = get_pool()
    redis = create_redis_client(settings)

    admins_repo = AdminRepository(pool)
    await seed_initial_owner(settings, admins_repo)

    application = build_application(settings=settings, redis=redis, pool=pool)
    application.bot_data["process_started_at"] = time.time()
    await application.initialize()
    await application.start()

    secret = settings.web_session_secret.get_secret_value() or "disabled"
    app.state.web = SimpleNamespace(
        sessions=SessionRepository(pool, secret=secret),
        audit=AuditRepository(pool),
        admins=application.bot_data["repos"]["admins"],
        cache=ConfigCache(redis, prefix=f"{settings.redis_rate_prefix}cfg:", ttl_seconds=settings.config_cache_ttl_seconds),
    )

    stop_event = asyncio.Event()
    bc_svc = application.bot_data["services"]["broadcast"]

    tasks: list[asyncio.Task] = []
    if not settings.run_embedded_workers:
        logger.info("RUN_EMBEDDED_WORKERS=false — this process will not start background workers")
    else:
        tasks = [
            asyncio.create_task(
                broadcast_worker_loop(
                    bot=application.bot,
                    redis=redis,
                    settings=settings,
                    broadcasts=application.bot_data["repos"]["broadcasts"],
                    users=application.bot_data["repos"]["users"],
                    bc_service=bc_svc,
                    stop_event=stop_event,
                ),
                name="broadcast-worker",
            ),
            asyncio.create_task(
                scheduler_worker_loop(
                    bot=application.bot,
                    settings=settings,
                    scheduled_repo=application.bot_data["repos"]["scheduled"],
                    broadcasts_repo=application.bot_data["repos"]["broadcasts"],
                    broadcast_service=bc_svc,
                    users=application.bot_data["repos"]["users"],
                    stop_event=stop_event,
                ),
                name="scheduler-worker",
            ),
            asyncio.create_task(
                retention_worker_loop(
                    bot=application.bot,
                    settings=settings,
                    settings_repo=application.bot_data["repos"]["settings"],
                    users=application.bot_data["repos"]["users"],
                    retention=application.bot_data["services"]["retention"],
                    stop_event=stop_event,
                ),
                name="retention-worker",
            ),
            asyncio.create_task(
                onboarding_worker_loop(
                    bot=application.bot,
                    settings=settings,
                    onboarding=application.bot_data["repos"]["onboarding"],
                    users=application.bot_data["repos"]["users"],
                    settings_repo=application.bot_data["repos"]["settings"],
                    stop_event=stop_event,
                ),
                name="onboarding-worker",
            ),
        ]

    app.state.settings = settings
    app.state.ptb = application
    app.state.redis = redis
    app.state.stop_event = stop_event
    app.state.worker_tasks = tasks

    async def _worker_heartbeat() -> None:
        while not stop_event.is_set():
            now = str(time.time())
            try:
                for task in tasks:
                    state = "stopped" if task.done() else "running"
                    await redis.hset("workers:status", task.get_name(), state)
                    await redis.hset("workers:heartbeat", task.get_name(), now)
                await redis.expire("workers:status", 120)
                await redis.expire("workers:heartbeat", 120)
            except Exception:
                logger.exception("worker heartbeat failed")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=10)
            except asyncio.TimeoutError:
                continue

    heartbeat_task = asyncio.create_task(_worker_heartbeat(), name="worker-heartbeat")

    # Register webhook in the background so Uvicorn can bind and /health works while
    # Telegram retries setWebhook (can take ~30s). Startup used to block on await here.
    webhook_task: asyncio.Task | None = None

    async def _webhook_registration_job() -> None:
        try:
            ok = await _register_webhook(application, settings)
            app.state.webhook_registered = ok
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Webhook registration task crashed")
            app.state.webhook_registered = False

    if settings.webhook_register_on_startup:
        app.state.webhook_registered = None  # pending until background task finishes
        webhook_task = asyncio.create_task(_webhook_registration_job())
        app.state.webhook_register_task = webhook_task
    else:
        logger.warning("WEBHOOK_REGISTER_ON_STARTUP=false — skipping setWebhook (dev mode)")
        app.state.webhook_registered = False
        app.state.webhook_register_task = None

    yield

    if webhook_task:
        webhook_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await webhook_task

    stop_event.set()
    heartbeat_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await heartbeat_task
    for t in tasks:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t

    await application.stop()
    await application.shutdown()
    await redis.aclose()
    await close_pool()


def _maybe_sentry(settings: Settings) -> None:
    dsn = (settings.sentry_dsn or "").strip()
    if not dsn:
        return
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed")
        return
    sentry_sdk.init(dsn=dsn, send_default_pii=False, traces_sample_rate=0.0)


def create_app() -> FastAPI:
    from utils.logging import setup_logging

    settings = get_settings()
    setup_logging(settings.log_level, json_logs=settings.log_json)
    _maybe_sentry(settings)
    app = FastAPI(title="Telegram Community Bot", lifespan=lifespan)
    app.include_router(router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://telegram.org 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https://telegram.org https://t.me; "
            "frame-src https://oauth.telegram.org https://telegram.org; "
            "connect-src 'self'"
        )
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        if isinstance(exc, StarletteHTTPException):
            return await http_exception_handler(request, exc)
        logger.exception("unhandled error")
        return JSONResponse({"detail": "Internal error"}, status_code=500)

    @app.get("/health")
    async def health(request: Request) -> dict[str, str]:
        reg = getattr(request.app.state, "webhook_registered", None)
        body: dict[str, str] = {"status": "ok"}
        if reg is None:
            body["webhook"] = "registering"
        elif reg is False:
            body["webhook"] = "not_registered"
        else:
            body["webhook"] = "registered"
        return body

    @app.get("/metrics")
    async def metrics(request: Request) -> Response:
        token = settings.metrics_token
        if token is not None and token.get_secret_value():
            expected = f"Bearer {token.get_secret_value()}"
            provided = request.headers.get("authorization") or ""
            if not hmac.compare_digest(provided, expected):
                raise HTTPException(status_code=401, detail="Unauthorized")
        return Response(METRICS.render(), media_type="text/plain; version=0.0.4")

    route_path = settings.webhook_path.format(secret=settings.webhook_secret.get_secret_value())

    @app.post(route_path)
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> dict[str, bool]:
        s: Settings = request.app.state.settings
        started = time.perf_counter()
        expected = (
            s.telegram_webhook_secret_token.get_secret_value()
            if s.telegram_webhook_secret_token
            else None
        )
        if not webhook_secret_ok(
            expected=expected,
            provided=x_telegram_bot_api_secret_token,
            required=s.webhook_require_secret_token,
        ):
            METRICS.inc("webhook_rejected_total")
            raise HTTPException(status_code=403, detail="Invalid webhook secret header")

        raw = await request.body()
        if len(raw) > s.webhook_max_body_bytes:
            METRICS.inc("webhook_rejected_total")
            raise HTTPException(status_code=413, detail="Payload too large")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON") from None
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON")

        update_id = data.get("update_id")
        if update_id is not None:
            claimed = await request.app.state.redis.set(
                dedup_key(s.redis_rate_prefix, int(update_id)),
                "1",
                nx=True,
                ex=172800,
            )
            if not claimed:
                METRICS.inc("webhook_duplicates_total")
                return {"ok": True}
            try:
                pool = get_pool()
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO processed_telegram_updates (update_id)
                        VALUES ($1)
                        ON CONFLICT (update_id) DO NOTHING;
                        """,
                        int(update_id),
                    )
            except Exception:
                logger.exception("Could not persist Telegram update id")

        kind = update_type_name(data)
        logger.info("webhook update_id=%s type=%s", update_id, kind)
        cjr = data.get("chat_join_request")
        if isinstance(cjr, dict):
            ch = cjr.get("chat") or {}
            frm = cjr.get("from") or {}
            logger.info(
                "Webhook JSON contains chat_join_request update_id=%s chat_id=%s user_id=%s",
                data.get("update_id"),
                ch.get("id"),
                frm.get("id"),
            )
        cm = data.get("chat_member")
        if isinstance(cm, dict):
            ch = cm.get("chat") or {}
            frm = cm.get("from") or {}
            old_m = cm.get("old_chat_member") or {}
            new_m = cm.get("new_chat_member") or {}
            logger.info(
                "Webhook JSON contains chat_member update_id=%s chat_id=%s actor_user_id=%s %s -> %s",
                data.get("update_id"),
                ch.get("id"),
                frm.get("id"),
                old_m.get("status"),
                new_m.get("status"),
            )
        application = request.app.state.ptb
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        METRICS.inc("webhook_updates_total")
        METRICS.observe("webhook_latency_seconds", time.perf_counter() - started)
        return {"ok": True}

    return app


app = create_app()


def main() -> None:
    _setup_uvloop()
    settings = get_settings()
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        factory=False,
    )


if __name__ == "__main__":
    main()

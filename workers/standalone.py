"""Run one background worker in its own process.

Usage: python -m workers.standalone broadcast|scheduler|onboarding|retention
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time

from bot.application import build_application, create_redis_client
from configs.settings import get_settings
from database.pool import close_pool, get_pool, init_pool
from utils.logging import setup_logging
from workers.broadcast_worker import broadcast_worker_loop
from workers.onboarding_worker import onboarding_worker_loop
from workers.retention_worker import retention_worker_loop
from workers.scheduler_worker import scheduler_worker_loop

logger = logging.getLogger(__name__)

KINDS = ("broadcast", "scheduler", "onboarding", "retention")


async def _heartbeat(redis, name: str, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        now = str(time.time())
        try:
            await redis.hset("workers:status", name, "running")
            await redis.hset("workers:heartbeat", name, now)
            await redis.expire("workers:status", 120)
            await redis.expire("workers:heartbeat", 120)
            with open("/tmp/worker-alive", "w", encoding="utf-8") as handle:
                handle.write(now)
        except Exception:
            logger.exception("worker heartbeat failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=10)
        except asyncio.TimeoutError:
            continue


async def _serve(kind: str) -> None:
    settings = get_settings()
    await init_pool(settings)
    pool = get_pool()
    redis = create_redis_client(settings)
    application = build_application(settings=settings, redis=redis, pool=pool)
    application.bot_data["process_started_at"] = time.time()
    await application.initialize()
    await application.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    beat = asyncio.create_task(_heartbeat(redis, f"{kind}-worker", stop_event), name="heartbeat")
    repos = application.bot_data["repos"]
    services = application.bot_data["services"]
    worker_name = f"{kind}-worker"
    if kind == "broadcast":
        worker = asyncio.create_task(
            broadcast_worker_loop(
                bot=application.bot,
                redis=redis,
                settings=settings,
                broadcasts=repos["broadcasts"],
                users=repos["users"],
                bc_service=services["broadcast"],
                stop_event=stop_event,
            ),
            name=worker_name,
        )
    elif kind == "scheduler":
        worker = asyncio.create_task(
            scheduler_worker_loop(
                bot=application.bot,
                settings=settings,
                scheduled_repo=repos["scheduled"],
                broadcasts_repo=repos["broadcasts"],
                broadcast_service=services["broadcast"],
                users=repos["users"],
                stop_event=stop_event,
            ),
            name=worker_name,
        )
    elif kind == "retention":
        worker = asyncio.create_task(
            retention_worker_loop(
                bot=application.bot,
                settings=settings,
                settings_repo=repos["settings"],
                users=repos["users"],
                retention=services["retention"],
                stop_event=stop_event,
            ),
            name=worker_name,
        )
    else:
        worker = asyncio.create_task(
            onboarding_worker_loop(
                bot=application.bot,
                settings=settings,
                onboarding=repos["onboarding"],
                users=repos["users"],
                settings_repo=repos["settings"],
                stop_event=stop_event,
            ),
            name=worker_name,
        )

    logger.info("Started %s", worker_name)
    await stop_event.wait()
    worker.cancel()
    beat.cancel()
    await asyncio.gather(worker, beat, return_exceptions=True)
    await application.stop()
    await application.shutdown()
    await redis.aclose()
    await close_pool()


def main() -> None:
    import sys

    kind = sys.argv[1] if len(sys.argv) > 1 else ""
    if kind not in KINDS:
        raise SystemExit(f"Usage: python -m workers.standalone {'|'.join(KINDS)}")
    settings = get_settings()
    setup_logging(settings.log_level, json_logs=settings.log_json)
    asyncio.run(_serve(kind))


if __name__ == "__main__":
    main()

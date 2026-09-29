"""Drain Postgres onboarding drip jobs and DM users."""

from __future__ import annotations

import asyncio
import logging

from telegram import Bot

from configs.settings import Settings
from database.repositories.onboarding_repo import OnboardingRepository
from database.repositories.settings_repo import SettingsRepository
from database.repositories.users import UserRepository
from services.outbound_sender import send_from_payload
from services.welcome_flow import substitute_name_in_payload
from utils.payload_coerce import coerce_payload_dict

logger = logging.getLogger(__name__)


async def onboarding_worker_loop(
    *,
    bot: Bot,
    settings: Settings,
    onboarding: OnboardingRepository,
    users: UserRepository,
    stop_event: asyncio.Event,
    settings_repo: SettingsRepository | None = None,
) -> None:
    tick = max(float(settings.retention_tick_seconds), 1.0)
    while not stop_event.is_set():
        enabled = settings.onboarding_drip_enabled
        if settings_repo is not None:
            try:
                channel = await settings_repo.get_channel_settings()
                if "onboarding_enabled" in channel:
                    enabled = enabled and bool(channel.get("onboarding_enabled"))
            except Exception:
                logger.exception("Onboarding enabled flag read failed")
        if not enabled:
            await asyncio.sleep(tick)
            continue

        try:
            rows = await onboarding.list_due_ready(limit=40)
        except Exception:
            logger.exception("Onboarding due query failed")
            await asyncio.sleep(tick)
            continue

        for row in rows:
            job_id = int(row["id"])
            uid = int(row["user_id"])
            raw_pl = coerce_payload_dict(row.get("payload"))
            if not raw_pl:
                await onboarding.mark_job_sent(job_id)
                continue
            fn = await users.get_first_name(uid)
            first_name = fn or ""
            pl = substitute_name_in_payload(raw_pl, first_name)
            try:
                await send_from_payload(bot, chat_id=uid, payload=pl)
            except Exception:
                logger.exception("Onboarding drip send failed job=%s user=%s", job_id, uid)
            else:
                await onboarding.mark_job_sent(job_id)

        await asyncio.sleep(tick)

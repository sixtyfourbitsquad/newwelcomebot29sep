"""Forward user traffic to admins' private chats and route replies back."""

from __future__ import annotations

import logging
from typing import Collection, FrozenSet, Optional

from redis.asyncio import Redis
from telegram import Bot, Message
from telegram.constants import ChatType
from telegram.error import Forbidden, TelegramError

from database.repositories.users import UserRepository
from models.domain import UserBroadcastStatus
from services.rate_limit_service import RateLimitService
from utils.flood import with_flood_wait

logger = logging.getLogger(__name__)


def union_admin_ids(env_ids: Collection[int], db_ids: Collection[int]) -> list[int]:
    """Every env admin and every active database admin, once, in stable order."""
    return sorted({int(x) for x in env_ids} | {int(x) for x in db_ids})


def user_id_from_forward_origin(origin: object) -> Optional[int]:
    """Read the original sender from a forwarded message, when Telegram includes it."""
    if origin is None:
        return None
    sender = getattr(origin, "sender_user", None)
    if sender is None:
        return None
    return int(sender.id)


def classify_message(message: Message) -> tuple[str, str, Optional[str]]:
    """Message type, short preview, and Telegram file id when there is media."""
    if message.text:
        return "text", message.text[:4000], None
    if message.photo:
        return "photo", (message.caption or "")[:4000], message.photo[-1].file_id
    if message.video:
        return "video", (message.caption or "")[:4000], message.video.file_id
    if message.voice:
        return "voice", (message.caption or "")[:4000], message.voice.file_id
    if message.document:
        name = message.document.file_name or ""
        return "document", (message.caption or name)[:4000], message.document.file_id
    if message.sticker:
        return "sticker", (message.sticker.emoji or "")[:32], message.sticker.file_id
    if message.audio:
        return "audio", (message.caption or "")[:4000], message.audio.file_id
    if message.animation:
        return "animation", (message.caption or "")[:4000], message.animation.file_id
    return "other", (message.caption or "")[:4000], None


class LiveChatService:
    """
    Forwards non-command user messages to every ADMIN_USER_IDS entry and every
    active database admin. This is a shared inbox: nobody is assigned a conversation.

    Admin replies in private chat use Telegram Reply. The original user is taken
    from the forwarded message (forward origin, then Redis, then Postgres).
    A normal admin message that is not a reply is not delivered to a user.
    """

    def __init__(
        self,
        *,
        admin_user_ids: Collection[int],
        users: UserRepository,
        redis: Redis,
        redis_mapping_prefix: str = "lcmap:",
        rate: RateLimitService,
        user_rate_per_minute: int = 30,
        admin_rate_per_minute: int = 120,
        admins=None,
        inbox=None,
    ) -> None:
        self._admin_ids: FrozenSet[int] = frozenset(int(x) for x in admin_user_ids)
        if not self._admin_ids:
            raise ValueError("admin_user_ids must not be empty")
        self._users = users
        self._redis = redis
        self._prefix = redis_mapping_prefix
        self._rate = rate
        self._user_rpm = user_rate_per_minute
        self._admin_rpm = admin_rate_per_minute
        self._admins = admins
        self._inbox = inbox

    def admin_ids(self) -> FrozenSet[int]:
        return self._admin_ids

    async def is_inbox_admin(self, user_id: int) -> bool:
        if int(user_id) in self._admin_ids:
            return True
        if self._admins is None:
            return False
        try:
            return await self._admins.is_active_admin(int(user_id))
        except Exception:
            logger.exception("active admin lookup failed")
            return False

    async def recipient_admin_ids(self) -> list[int]:
        db_ids: list[int] = []
        if self._admins is not None:
            try:
                db_ids = await self._admins.list_active_ids()
            except Exception:
                logger.exception("listing active admins failed; using ADMIN_USER_IDS only")
        return union_admin_ids(self._admin_ids, db_ids)

    def _map_key(self, inbox_chat_id: int, inbox_message_id: int) -> str:
        """Redis key for copy_message fallback (unique per admin inbox message)."""
        return f"{self._prefix}{inbox_chat_id}:{inbox_message_id}"

    async def forward_user_message(self, bot: Bot, message: Message) -> None:
        """Forward or copy user message to every env admin and every active database admin."""
        if message.chat.type != ChatType.PRIVATE:
            return
        uid = message.from_user.id if message.from_user else None
        if uid is None:
            return
        if await self.is_inbox_admin(uid):
            return
        ok = await self._rate.allow(f"user:{uid}", limit=self._user_rpm, window_seconds=60)
        if not ok:
            logger.info("Rate limited user %s", uid)
            return

        kind, preview, file_id = classify_message(message)
        inbox_id: Optional[int] = None
        if self._inbox is not None:
            try:
                inbox_id = await self._inbox.record_incoming(
                    telegram_user_id=uid,
                    original_message_id=message.message_id,
                    message_type=kind,
                    content_text=preview,
                    file_id=file_id,
                    payload={"chat_id": message.chat_id, "message_id": message.message_id},
                )
            except Exception:
                logger.exception("inbox record failed user_id=%s", uid)

        for aid in await self.recipient_admin_ids():
            forwarded_id, status, error = await self._forward_one_admin(bot, message, uid, aid)
            if inbox_id is None or self._inbox is None:
                continue
            try:
                await self._inbox.record_forward(
                    inbox_message_id=inbox_id,
                    admin_telegram_id=aid,
                    forwarded_message_id=forwarded_id,
                    delivery_status=status,
                    telegram_error=error,
                )
            except Exception:
                logger.exception("inbox forward record failed admin=%s", aid)

    async def _remember(self, admin_id: int, inbox_message_id: int, user_id: int) -> None:
        await self._redis.set(
            self._map_key(admin_id, inbox_message_id),
            str(user_id),
            ex=86400 * 7,
        )

    async def _forward_one_admin(
        self, bot: Bot, message: Message, user_id: int, admin_id: int
    ) -> tuple[Optional[int], str, Optional[str]]:
        try:
            fwd = await with_flood_wait(
                lambda a=admin_id: bot.forward_message(
                    chat_id=a,
                    from_chat_id=message.chat_id,
                    message_id=message.message_id,
                )
            )
            # Always map inbox message_id → user (Telegram may hide sender in forward_origin when replying).
            if fwd:
                await self._remember(admin_id, fwd.message_id, user_id)
                return int(fwd.message_id), "delivered", None
            return None, "failed", "empty forward"
        except Forbidden:
            logger.warning("Cannot forward to admin %s (blocked bot or cannot DM)", admin_id)
            return None, "failed", "forbidden"
        except TelegramError as e:
            logger.warning("Forward failed for admin %s: %s", admin_id, e)
            try:
                copied = await with_flood_wait(
                    lambda a=admin_id: bot.copy_message(
                        chat_id=a,
                        from_chat_id=message.chat_id,
                        message_id=message.message_id,
                    )
                )
                await self._remember(admin_id, copied.message_id, user_id)
                return int(copied.message_id), "delivered", None
            except TelegramError as copy_error:
                text = (message.text or message.caption or "")[:3500]
                try:
                    sent = await with_flood_wait(
                        lambda a=admin_id: bot.send_message(
                            chat_id=a,
                            text=f"[fallback] user `{user_id}`:\n{text}",
                            parse_mode="Markdown",
                        )
                    )
                    if sent:
                        await self._remember(admin_id, sent.message_id, user_id)
                        return int(sent.message_id), "delivered", None
                except TelegramError as fallback_error:
                    logger.exception("Fallback DM failed for admin %s", admin_id)
                    return None, "failed", str(fallback_error)[:500]
                return None, "failed", str(copy_error)[:500]

    async def relay_admin_reply(self, bot: Bot, message: Message) -> bool:
        """
        If message is a reply from an ENV-listed admin private inbox, deliver to end user.

        Returns True if handled.
        """
        if message.chat.type != ChatType.PRIVATE:
            return False
        if message.reply_to_message is None:
            return False
        actor = message.from_user.id if message.from_user else 0
        if not await self.is_inbox_admin(actor) or not await self.is_inbox_admin(message.chat_id):
            return False

        target_user_id: Optional[int] = None
        rmsg = message.reply_to_message
        target_user_id = user_id_from_forward_origin(rmsg.forward_origin)
        if target_user_id is None:
            mid = rmsg.message_id
            inbox_id = message.chat_id
            key = self._map_key(inbox_id, mid)
            raw = await self._redis.get(key)
            if raw:
                s = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                target_user_id = int(s)
        if target_user_id is None and self._inbox is not None:
            try:
                target_user_id = await self._inbox.user_for_forward(message.chat_id, rmsg.message_id)
            except Exception:
                logger.exception("inbox forward lookup failed")
        if target_user_id is None:
            logger.warning(
                "Admin reply: could not resolve user (reply to msg_id=%s in chat=%s)",
                rmsg.message_id,
                message.chat_id,
            )
            return False

        aid = message.from_user.id if message.from_user else 0
        ok = await self._rate.allow(f"admin:{aid}", limit=self._admin_rpm, window_seconds=60)
        if not ok:
            return True

        if self._inbox is not None:
            try:
                if await self._inbox.reply_exists(aid, message.message_id):
                    return True
            except Exception:
                logger.exception("inbox reply lookup failed")

        kind, preview, file_id = classify_message(message)
        sent_ok = False
        error_text: Optional[str] = None
        try:
            if message.text:
                await with_flood_wait(
                    lambda: bot.send_message(chat_id=target_user_id, text=message.text)
                )
            elif message.photo:
                p = message.photo[-1]
                await with_flood_wait(
                    lambda: bot.send_photo(
                        chat_id=target_user_id,
                        photo=p.file_id,
                        caption=message.caption,
                    )
                )
            elif message.video:
                await with_flood_wait(
                    lambda: bot.send_video(
                        chat_id=target_user_id,
                        video=message.video.file_id,
                        caption=message.caption,
                    )
                )
            elif message.voice:
                await with_flood_wait(
                    lambda: bot.send_voice(
                        chat_id=target_user_id,
                        voice=message.voice.file_id,
                        caption=message.caption,
                    )
                )
            elif message.audio:
                await with_flood_wait(
                    lambda: bot.send_audio(
                        chat_id=target_user_id,
                        audio=message.audio.file_id,
                        caption=message.caption,
                    )
                )
            elif message.document:
                await with_flood_wait(
                    lambda: bot.send_document(
                        chat_id=target_user_id,
                        document=message.document.file_id,
                        caption=message.caption,
                    )
                )
            elif message.animation:
                await with_flood_wait(
                    lambda: bot.send_animation(
                        chat_id=target_user_id,
                        animation=message.animation.file_id,
                        caption=message.caption,
                    )
                )
            elif message.sticker:
                await with_flood_wait(
                    lambda: bot.send_sticker(
                        chat_id=target_user_id,
                        sticker=message.sticker.file_id,
                    )
                )
            else:
                await with_flood_wait(
                    lambda: bot.copy_message(
                        chat_id=target_user_id,
                        from_chat_id=message.chat_id,
                        message_id=message.message_id,
                    )
                )
            sent_ok = True
        except Forbidden:
            error_text = "forbidden"
            await self._users.set_broadcast_status(target_user_id, UserBroadcastStatus.BLOCKED)
        except TelegramError as e:
            error_text = str(e)[:500]
            logger.error("Relay admin reply failed: %s", e)
        if self._inbox is not None:
            try:
                await self._inbox.record_reply(
                    inbox_message_id=None,
                    admin_telegram_id=aid,
                    target_user_id=target_user_id,
                    reply_message_id=None,
                    source_message_id=message.message_id,
                    message_type=kind,
                    content_text=preview,
                    payload={"file_id": file_id} if file_id else {},
                    delivery_status="delivered" if sent_ok else "failed",
                    telegram_error=error_text,
                )
            except Exception:
                logger.exception("inbox reply record failed")
        return True

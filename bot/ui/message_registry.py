"""Учёт message_id бота для очистки чата."""

from __future__ import annotations

import logging
from collections import defaultdict

from aiogram import Bot

logger = logging.getLogger(__name__)

_MAX_PER_USER = 80
_user_messages: dict[int, list[int]] = defaultdict(list)


def register_bot_message(user_id: int, message_id: int) -> None:
    if not user_id or not message_id:
        return
    bucket = _user_messages[user_id]
    bucket.append(message_id)
    if len(bucket) > _MAX_PER_USER:
        _user_messages[user_id] = bucket[-_MAX_PER_USER:]


def register_bot_messages(user_id: int, message_ids: list[int]) -> None:
    for mid in message_ids:
        register_bot_message(user_id, mid)


async def cleanup_user_chat(bot: Bot, chat_id: int, user_id: int) -> int:
    """Удаляет отслеживаемые сообщения бота. Возвращает число удалённых."""
    ids = list(_user_messages.get(user_id, []))
    _user_messages[user_id] = []
    deleted = 0
    for mid in ids:
        try:
            await bot.delete_message(chat_id, mid)
            deleted += 1
        except Exception:
            pass
    logger.info("Chat cleanup user=%s deleted=%s/%s", user_id, deleted, len(ids))
    return deleted

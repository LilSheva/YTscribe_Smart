"""Middleware: ошибка handler'а не останавливает бота."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject, Update

from bot.middleware.error_notify import notify_update_failure

logger = logging.getLogger(__name__)


class HandlerResilienceMiddleware(BaseMiddleware):
    """
    Ловит необработанные исключения в handlers.
    Логирует, уведомляет пользователя, polling продолжается.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception as exc:
            logger.exception("Unhandled handler error: %s", exc)
            update: Update | None = data.get("event_update")
            if isinstance(event, Update):
                update = event
            bot: Bot | None = data.get("bot")
            if update and bot:
                await notify_update_failure(bot, update, exc)
            return None

"""Уведомление пользователя при сбоях handler'ов."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import CallbackQuery, Message, Update

from bot.keyboards.main_menu import get_back_home_keyboard
from bot.ui import screens
from bot.ui.telegram_safe import safe_callback_answer, safe_edit_text, safe_send_message

logger = logging.getLogger(__name__)

_USER_MESSAGE = (
    "Операция не выполнена — бот продолжает работать.\n"
    "Попробуйте ещё раз или вернитесь в главное меню."
)


async def notify_update_failure(bot: Bot, update: Update, exc: Exception) -> None:
    """Показывает ошибку пользователю, не роняя polling."""
    technical = str(exc)[:200]
    text = screens.error_text("Сбой", _USER_MESSAGE, technical)
    markup = get_back_home_keyboard()

    try:
        if update.callback_query:
            cq: CallbackQuery = update.callback_query
            await safe_callback_answer(
                cq,
                "Не удалось выполнить действие",
                show_alert=True,
            )
            if cq.message:
                await safe_edit_text(
                    cq.message,
                    text,
                    reply_markup=markup,
                    parse_mode="HTML",
                )
            return

        if update.message:
            msg: Message = update.message
            await safe_send_message(
                bot,
                msg.chat.id,
                text,
                reply_markup=markup,
                parse_mode="HTML",
            )
    except Exception as notify_err:
        logger.warning("notify_update_failure failed: %s", notify_err)

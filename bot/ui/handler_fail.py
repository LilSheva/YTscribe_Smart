"""Единый отчёт об ошибках в UI (handler не падает)."""

from __future__ import annotations

import logging
from typing import Any

from aiogram.types import CallbackQuery

from bot.keyboards.main_menu import get_back_home_keyboard
from bot.ui import screens
from bot.ui.telegram_safe import safe_callback_answer, safe_edit_text

logger = logging.getLogger(__name__)

_DEFAULT_USER_MSG = "Операция не выполнена. Бот работает — попробуйте ещё раз."


async def report_ui_failure(
    target: Any,
    stage: str,
    *,
    user_message: str = _DEFAULT_USER_MSG,
    technical: str = "",
    reply_markup=None,
) -> None:
    markup = reply_markup or get_back_home_keyboard()
    await safe_edit_text(
        target,
        screens.error_text(stage, user_message, technical),
        reply_markup=markup,
        parse_mode="HTML",
    )


async def report_callback_failure(
    callback: CallbackQuery,
    stage: str,
    exc: BaseException | None = None,
    *,
    reply_markup=None,
) -> None:
    if exc:
        logger.exception("Callback failed [%s]: %s", stage, exc)
        technical = str(exc)[:200]
    else:
        logger.error("Callback failed [%s]", stage)
        technical = ""
    await safe_callback_answer(callback, f"{stage}: ошибка", show_alert=True)
    if callback.message:
        await report_ui_failure(
            callback.message,
            stage,
            technical=technical,
            reply_markup=reply_markup,
        )

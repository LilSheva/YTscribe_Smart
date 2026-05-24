"""Якорная inline-панель главного меню (одно сообщение на пользователя)."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.types import Message

from bot.ui.nav import EditTarget

logger = logging.getLogger(__name__)

# user_id -> (chat_id, message_id)
_panels: dict[int, tuple[int, int]] = {}


def set_panel(user_id: int, chat_id: int, message_id: int) -> None:
    if user_id:
        _panels[user_id] = (chat_id, message_id)


def get_panel(user_id: int) -> tuple[int, int] | None:
    return _panels.get(user_id)


def clear_panel(user_id: int) -> None:
    _panels.pop(user_id, None)


def panel_target(bot: Bot, user_id: int) -> EditTarget | None:
    coords = get_panel(user_id)
    if not coords:
        return None
    chat_id, message_id = coords
    return EditTarget(bot, chat_id, message_id)


async def edit_panel(
    bot: Bot,
    user_id: int,
    text: str,
    *,
    reply_markup=None,
    parse_mode: str = "HTML",
    **kwargs: Any,
) -> bool:
    target = panel_target(bot, user_id)
    if not target:
        return False
    try:
        await target.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
            **kwargs,
        )
        return True
    except Exception as e:
        logger.warning("Panel edit failed user=%s: %s", user_id, e)
        return False


async def show_home(
    bot: Bot,
    user_id: int,
    chat_id: int,
    message_id: int,
    user_name: str = "друг",
) -> None:
    from bot.keyboards.main_menu import get_home_keyboard
    from bot.ui import screens

    set_panel(user_id, chat_id, message_id)
    from bot.ui.nav import EditTarget
    from bot.ui.telegram_safe import safe_edit_text

    await safe_edit_text(
        EditTarget(bot, chat_id, message_id),
        screens.home_text(user_name),
        reply_markup=get_home_keyboard(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def ensure_panel_message(message: Message) -> EditTarget:
    """Возвращает EditTarget панели; создаёт новую, если якоря нет."""
    user = message.from_user
    if not user:
        raise ValueError("no user")
    coords = get_panel(user.id)
    if coords:
        return EditTarget(message.bot, coords[0], coords[1])
    sent = await message.answer("⏳", parse_mode=None)
    set_panel(user.id, sent.chat.id, sent.message_id)
    return EditTarget(message.bot, sent.chat.id, sent.message_id)

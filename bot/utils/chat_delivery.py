"""Доставка длинных текстов в Telegram-чат."""

from __future__ import annotations

from aiogram import Bot
from aiogram.types import FSInputFile, Message

from services.llm_router import split_for_telegram
from bot.ui.nav import track_ephemeral
from bot.ui.message_registry import register_bot_message


async def send_text_parts(
    bot: Bot,
    chat_id: int,
    text: str,
    *,
    header: str = "",
    task_data: dict | None = None,
    user_id: int | None = None,
) -> list[Message]:
    """Отправляет текст частями; помечает сообщения как ephemeral."""
    parts = split_for_telegram(text)
    sent: list[Message] = []
    for i, part in enumerate(parts):
        prefix = ""
        if header and i == 0:
            prefix = f"{header}\n\n"
        elif len(parts) > 1:
            prefix = f"({i + 1}/{len(parts)})\n\n"
        msg = await bot.send_message(chat_id, prefix + part, parse_mode=None)
        sent.append(msg)
        if task_data is not None:
            track_ephemeral(task_data, msg.message_id)
        if user_id:
            register_bot_message(user_id, msg.message_id)
    return sent


async def send_document_file(
    bot: Bot,
    chat_id: int,
    path,
    *,
    caption: str = "",
    task_data: dict | None = None,
    user_id: int | None = None,
) -> Message:
    msg = await bot.send_document(
        chat_id,
        FSInputFile(path),
        caption=caption or None,
    )
    if task_data is not None:
        track_ephemeral(task_data, msg.message_id)
    if user_id:
        register_bot_message(user_id, msg.message_id)
    return msg


async def send_export_hint(
    bot: Bot,
    chat_id: int,
    task_id: str,
    *,
    task_data: dict,
    user_id: int,
) -> Message:
    from bot.keyboards.main_menu import get_export_hide_keyboard

    msg = await bot.send_message(
        chat_id,
        "📤 Экспорт отправлен.",
        reply_markup=get_export_hide_keyboard(task_id),
        parse_mode=None,
    )
    track_ephemeral(task_data, msg.message_id)
    register_bot_message(user_id, msg.message_id)
    return msg


send_markdown_file = send_document_file

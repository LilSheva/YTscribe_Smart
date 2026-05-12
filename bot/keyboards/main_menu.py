"""
bot/keyboards/main_menu.py — Динамические инлайн-клавиатуры.

Кнопки рендерятся только для включённых модулей (Feature Toggles).
"""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.config import features


def get_media_keyboard(task_id: str) -> InlineKeyboardMarkup:
    """
    Клавиатура действий после получения метаданных видео.

    Args:
        task_id: Короткий идентификатор задачи (для callback_data).

    Returns:
        InlineKeyboardMarkup с кнопками доступных действий.
    """
    builder = InlineKeyboardBuilder()

    if features.downloader:
        builder.button(text="🎵 Скачать M4A", callback_data=f"dl_m4a:{task_id}")
        builder.button(text="🎬 Скачать MP4", callback_data=f"dl_mp4:{task_id}")

    if features.transcript:
        builder.button(text="📝 Транскрибировать", callback_data=f"transcript:{task_id}")

    if features.llm:
        builder.button(text="🧠 AI Саммари", callback_data=f"llm_analyze:{task_id}")

    # Раскладка: 2 кнопки в ряд
    builder.adjust(2)
    return builder.as_markup()


def get_post_download_keyboard(task_id: str, can_send_to_chat: bool) -> InlineKeyboardMarkup:
    """
    Клавиатура после успешной загрузки на GDrive.

    Args:
        task_id: Идентификатор задачи.
        can_send_to_chat: True если файл <= 50MB (можно отправить в Telegram).

    Returns:
        InlineKeyboardMarkup с опциональной кнопкой отправки в чат.
    """
    builder = InlineKeyboardBuilder()

    if can_send_to_chat:
        builder.button(text="📥 Получить в чат", callback_data=f"send_chat:{task_id}")

    builder.adjust(1)
    return builder.as_markup()

"""Клавиатуры для пакетного импорта из .txt."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_batch_confirm_keyboard(batch_id: str, count: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=f"▶️ Обработать {count} видео", callback_data=f"batch_start:{batch_id}")
    builder.button(text="❌ Отмена", callback_data=f"batch_cancel:{batch_id}")
    builder.adjust(1)
    return builder.as_markup()

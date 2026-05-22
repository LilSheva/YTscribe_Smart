"""
bot/keyboards/main_menu.py — Динамические инлайн-клавиатуры.

Кнопки рендерятся только для включённых модулей (Feature Toggles).
"""

from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
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
        builder.button(text="🧠 AI Анализ", callback_data=f"llm_variants:{task_id}")

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


def get_analysis_variants_keyboard(task_id: str, variants: list[dict]) -> InlineKeyboardMarkup:
    """Кнопки выбора режима анализа."""
    builder = InlineKeyboardBuilder()
    for v in variants:
        builder.button(text=v["label"], callback_data=f"analyze_run:{task_id}:{v['idx']}")
    builder.adjust(1)
    return builder.as_markup()


def get_analysis_menu_keyboard(task_id: str, variants: list[dict], past_results: list[dict]) -> InlineKeyboardMarkup:
    """Показывает варианты анализа + прошлые ответы + кнопку обновления."""
    builder = InlineKeyboardBuilder()
    # Прошлые ответы сверху
    for r in past_results:
        label_short = r["label"][:40]
        dt = r["created_at"][5:16]  # MM-DD HH:MM
        builder.button(text=f"📄 {label_short} ({dt})", callback_data=f"view_result:{r['id']}")
    # Варианты для запуска
    for v in variants:
        builder.button(text=v["label"], callback_data=f"analyze_run:{task_id}:{v['idx']}")
    # Кнопка обновить
    builder.button(text="🔄 Обновить с комментарием", callback_data=f"variants_refresh:{task_id}")
    builder.adjust(1)
    return builder.as_markup()


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📥 Новая ссылка"), KeyboardButton(text="📜 История")],
            [KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
        persistent=True,
    )


def get_history_item_keyboard(entry_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🧠 Анализ", callback_data=f"hist_analyze:{entry_id}")
    builder.button(text="☁️ GDrive", callback_data=f"hist_gdrive:{entry_id}")
    builder.button(text="🗑 Удалить", callback_data=f"hist_delete:{entry_id}")
    builder.button(text="◀️ Назад", callback_data="hist_list:0")
    builder.adjust(3, 1)
    return builder.as_markup()


def get_history_list_keyboard(entries: list, page: int = 0, page_size: int = 5) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    start = page * page_size
    for e in entries[start:start + page_size]:
        mins = e.duration_sec // 60
        builder.button(text=f"{e.title[:35]} ({mins}м)", callback_data=f"hist_item:{e.id}")
    builder.adjust(1)
    if page > 0:
        builder.button(text="◀️", callback_data=f"hist_list:{page-1}")
    if start + page_size < len(entries):
        builder.button(text="▶️", callback_data=f"hist_list:{page+1}")
    builder.adjust(1)
    return builder.as_markup()

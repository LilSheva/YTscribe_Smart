"""
bot/keyboards/main_menu.py — Динамические инлайн-клавиатуры.

Кнопки рендерятся только для включённых модулей (Feature Toggles).
"""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.config import features
from utils.history_format import format_history_list_label

MAX_PAST_RESULTS = 5
MAX_VARIANTS = 6


def get_home_keyboard() -> InlineKeyboardMarkup:
    """Главное inline-меню (единая панель)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📥 Ссылка", callback_data="menu:new_link")
    builder.button(text="📜 История", callback_data="menu:history")
    builder.button(text="📦 Пакет", callback_data="menu:batch")
    builder.button(text="⚙️ Настройки", callback_data="menu:settings")
    builder.button(text="☁️ GDrive", callback_data="menu:gdrive")
    builder.button(text="🧹 Очистить", callback_data="menu:clean")
    builder.button(text="ℹ️ Помощь", callback_data="menu:help")
    builder.adjust(1, 2, 2, 2, 1)
    return builder.as_markup()


def get_back_home_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Главная", callback_data="menu:home")
    return builder.as_markup()


def get_media_keyboard(video_id: str, has_transcript: bool = False) -> InlineKeyboardMarkup:
    """Карточка видео / экран после транскрипта."""
    builder = InlineKeyboardBuilder()

    if features.downloader:
        builder.button(text="🎵 M4A", callback_data=f"dl_m4a:{video_id}")
        builder.button(text="🎬 MP4", callback_data=f"dl_mp4:{video_id}")

    if features.transcript and not has_transcript:
        builder.button(text="📝 Транскрибировать", callback_data=f"transcript:{video_id}")

    if features.llm and has_transcript:
        builder.button(text="🧠 AI", callback_data=f"nav:analysis:{video_id}")

    if has_transcript:
        builder.button(text="📤 Экспорт", callback_data=f"reveal_transcript:{video_id}")

    builder.button(text="◀️ Главная", callback_data="menu:home")

    if features.downloader:
        builder.adjust(2, 1, 1, 1, 1)
    else:
        builder.adjust(1)
    return builder.as_markup()


def get_post_download_keyboard(video_id: str, can_send_to_chat: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if can_send_to_chat:
        builder.button(text="📥 В чат", callback_data=f"send_chat:{video_id}")

    builder.button(text="◀️ К видео", callback_data=f"nav:card:{video_id}")
    builder.button(text="◀️ Главная", callback_data="menu:home")
    builder.adjust(1)
    return builder.as_markup()


def get_analysis_menu_keyboard(
    video_id: str,
    variants: list[dict],
    past_results: list[dict],
    *,
    back_callback: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    recent = past_results[-MAX_PAST_RESULTS:]
    for r in reversed(recent):
        label_short = r["label"][:32]
        dt = r["created_at"][5:16]
        builder.button(
            text=f"📄 {label_short} ({dt})",
            callback_data=f"view_result:{video_id}:{r['id']}",
        )

    shown_variants = variants[:MAX_VARIANTS]
    for v in shown_variants:
        builder.button(text=v["label"], callback_data=f"analyze_run:{video_id}:{v['idx']}")

    builder.button(text="🔄 Новые варианты…", callback_data=f"variants_refresh:{video_id}")
    builder.button(
        text="◀️ К видео",
        callback_data=back_callback or f"nav:transcript:{video_id}",
    )

    if recent and shown_variants:
        builder.adjust(1, 2, 1, 1)
    elif shown_variants:
        builder.adjust(2, 1, 1)
    else:
        builder.adjust(1)
    return builder.as_markup()


def get_ai_result_keyboard(video_id: str, result_id: int) -> InlineKeyboardMarkup:
    """Экран результата AI (новый или из истории прошлых)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Спросить", callback_data=f"ai_chat_start:{video_id}:{result_id}")
    builder.button(text="📤 Экспорт", callback_data=f"reveal_llm:{video_id}:{result_id}")
    builder.button(text="◀️ AI", callback_data=f"nav:analysis:{video_id}")
    builder.button(text="◀️ Видео", callback_data=f"nav:card:{video_id}")
    builder.adjust(2, 2)
    return builder.as_markup()


def get_ai_chat_keyboard(video_id: str, result_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="📜 Полный транскрипт",
        callback_data=f"ai_chat_full:{video_id}:{result_id}",
    )
    builder.button(text="❌ Завершить", callback_data=f"ai_chat_done:{video_id}:{result_id}")
    builder.adjust(1)
    return builder.as_markup()


def get_export_hide_keyboard(video_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🧹 Скрыть", callback_data=f"ui:hide_export:{video_id}")
    return builder.as_markup()


def get_fsm_cancel_keyboard(video_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=f"fsm_cancel:{video_id}")
    return builder.as_markup()


def get_main_reply_keyboard():
    """Deprecated: используйте get_home_keyboard()."""
    return get_home_keyboard()

def get_gdrive_sync_keyboard(
    affected_video_ids: list[str],
    *,
    missing_summary_count: int = 0,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if affected_video_ids:
        builder.button(
            text=f"🔧 Исправить sync ({len(affected_video_ids)})",
            callback_data="gdrive_sync:repair",
        )
    if missing_summary_count > 0:
        builder.button(
            text=f"🧠 Добить саммари ({missing_summary_count})",
            callback_data="gdrive_sync:summaries",
        )
    builder.button(text="🔄 Проверить снова", callback_data="gdrive_sync:refresh")
    builder.button(text="◀️ Главная", callback_data="menu:home")
    builder.adjust(1)
    return builder.as_markup()


def get_error_keyboard(
    *,
    retry_callback: str | None = None,
    back_callback: str | None = None,
    close: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if retry_callback:
        builder.button(text="🔄 Повторить", callback_data=retry_callback)
    if back_callback:
        builder.button(text="◀️ Назад", callback_data=back_callback)
    if close:
        builder.button(text="◀️ Главная", callback_data="menu:home")
    builder.adjust(1)
    return builder.as_markup()


def get_settings_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 Транскрипт в чате", callback_data="settings:toggle_transcript")
    builder.button(text="🧠 AI-ответ в чате", callback_data="settings:toggle_llm")
    builder.button(text="🎙 Модель Whisper", callback_data="settings:pick_whisper")
    builder.button(text="🧠 Модель LLM", callback_data="settings:pick_llm")
    builder.button(text="🌐 Язык транскрибации", callback_data="settings:cycle_lang")
    builder.button(text="↩️ Сбросить к defaults", callback_data="settings:reset")
    builder.button(text="◀️ Главная", callback_data="menu:home")
    builder.adjust(1)
    return builder.as_markup()


def get_whisper_picker_keyboard(current_model: str) -> InlineKeyboardMarkup:
    from services.transcriber import get_available_models

    builder = InlineKeyboardBuilder()
    for idx, (model_id, label) in enumerate(get_available_models().items()):
        mark = "✓ " if model_id == current_model else ""
        builder.button(
            text=f"{mark}{label[:40]}",
            callback_data=f"settings:w:{idx}",
        )
    builder.button(text="◀️ Назад", callback_data="settings:main")
    builder.adjust(1)
    return builder.as_markup()


def get_llm_picker_keyboard(current_model: str) -> InlineKeyboardMarkup:
    from services.llm_router import get_available_models

    builder = InlineKeyboardBuilder()
    for idx, (model_id, label) in enumerate(get_available_models().items()):
        mark = "✓ " if model_id == current_model else ""
        builder.button(
            text=f"{mark}{label[:40]}",
            callback_data=f"settings:l:{idx}",
        )
    builder.button(text="◀️ Назад", callback_data="settings:main")
    builder.adjust(1)
    return builder.as_markup()


def get_history_item_keyboard(video_id: str, *, page: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🧠 AI", callback_data=f"hist_analyze:{video_id}:{page}")
    builder.button(text="☁️ GDrive", callback_data=f"hist_gdrive:{video_id}")
    builder.button(text="🗑 Удалить", callback_data=f"hist_delete:{video_id}:{page}")
    builder.button(text="◀️ Назад", callback_data=f"hist_list:{page}")
    builder.adjust(3, 1)
    return builder.as_markup()


def get_history_list_keyboard(entries: list, page: int = 0, page_size: int = 5) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    start = page * page_size
    for i, e in enumerate(entries[start:start + page_size]):
        builder.button(
            text=format_history_list_label(e, index=start + i + 1),
            callback_data=f"hist_item:{e.video_id}:{page}",
        )
    nav_row: list = []
    if page > 0:
        nav_row.append(("◀️", f"hist_list:{page - 1}"))
    if start + page_size < len(entries):
        nav_row.append(("▶️", f"hist_list:{page + 1}"))
    for label, data in nav_row:
        builder.button(text=label, callback_data=data)
    builder.button(text="◀️ Главная", callback_data="menu:home")
    builder.adjust(1)
    return builder.as_markup()

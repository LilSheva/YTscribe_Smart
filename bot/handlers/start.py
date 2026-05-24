"""
bot/handlers/start.py — /start, inline-панель, история, настройки.
"""

from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove

from core.config import ENABLE_KB
from core.models import MediaTask
from bot.keyboards.main_menu import (
    get_home_keyboard,
    get_back_home_keyboard,
    get_history_list_keyboard,
    get_history_item_keyboard,
    get_error_keyboard,
)
from bot.ui import screens
from bot.ui.nav import delete_message_safe
from bot.ui.panel import set_panel, get_panel, show_home
from bot.ui.message_registry import register_bot_message, cleanup_user_chat
from utils.history_format import format_history_item_header
from services import db
from services import user_settings
from services import llm_router
from services import timing_stats
from services.transcriber import get_available_models
from bot.ui.settings_ui import render_settings_message, render_whisper_picker, render_llm_picker
from bot.ui.handler_fail import report_callback_failure
from bot.ui.telegram_safe import safe_edit_text, safe_callback_answer

router = Router(name="start")
logger = logging.getLogger(__name__)


async def _remove_reply_keyboard(message: Message) -> None:
    try:
        tmp = await message.answer("…", reply_markup=ReplyKeyboardRemove())
        await delete_message_safe(message.bot, message.chat.id, tmp.message_id)
    except Exception:
        pass


async def show_home_on(target: Message, user_id: int, user_name: str = "друг") -> None:
    await show_home(target.bot, user_id, target.chat.id, target.message_id, user_name)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if not message.from_user:
        return
    await _remove_reply_keyboard(message)
    user_name = message.from_user.first_name or "друг"
    sent = await message.answer(
        screens.home_text(user_name),
        reply_markup=get_home_keyboard(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    set_panel(message.from_user.id, sent.chat.id, sent.message_id)
    register_bot_message(message.from_user.id, sent.message_id)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    if not message.from_user:
        return
    coords = get_panel(message.from_user.id)
    text = screens.help_text()
    markup = get_back_home_keyboard()
    if coords:
        chat_id, msg_id = coords
        try:
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                reply_markup=markup,
                parse_mode="HTML",
            )
            await delete_message_safe(message.bot, message.chat.id, message.message_id)
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data == "menu:home")
@router.callback_query(F.data == "ui:close")
async def cb_menu_home(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    name = callback.from_user.first_name or "друг"
    await show_home_on(callback.message, callback.from_user.id, name)
    await callback.answer()


@router.callback_query(F.data == "menu:new_link")
async def cb_menu_new_link(callback: CallbackQuery) -> None:
    set_panel(callback.from_user.id, callback.message.chat.id, callback.message.message_id)
    await safe_edit_text(
        callback.message,
        screens.new_link_prompt_text(),
        reply_markup=get_back_home_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "menu:help")
async def cb_menu_help(callback: CallbackQuery) -> None:
    set_panel(callback.from_user.id, callback.message.chat.id, callback.message.message_id)
    await safe_edit_text(
        callback.message,
        screens.help_text(),
        reply_markup=get_back_home_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "menu:history")
async def cb_menu_history(callback: CallbackQuery) -> None:
    set_panel(callback.from_user.id, callback.message.chat.id, callback.message.message_id)
    entries = db.list_videos(callback.from_user.id)
    if not entries:
        await safe_edit_text(
            callback.message,
            "📜 История пуста. Транскрибируй видео — оно появится здесь.",
            reply_markup=get_back_home_keyboard(),
            parse_mode=None,
        )
    else:
        keyboard = get_history_list_keyboard(entries, page=0)
        await safe_edit_text(
            callback.message,
            f"📜 История ({len(entries)} записей):",
            reply_markup=keyboard,
            parse_mode=None,
        )
    await callback.answer()


@router.callback_query(F.data == "menu:settings")
async def cb_menu_settings(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    set_panel(callback.from_user.id, callback.message.chat.id, callback.message.message_id)
    settings = user_settings.get_user_settings(callback.from_user.id)
    text, markup = render_settings_message(settings)
    await safe_edit_text(callback.message, text, reply_markup=markup, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "menu:gdrive")
async def cb_menu_gdrive(callback: CallbackQuery) -> None:
    from bot.handlers.gdrive_sync_handler import run_gdrive_sync_panel

    set_panel(callback.from_user.id, callback.message.chat.id, callback.message.message_id)
    await safe_callback_answer(callback, "Проверяю…")
    try:
        await run_gdrive_sync_panel(callback.message, callback.from_user.id)
    except Exception as e:
        await report_callback_failure(callback, "GDrive sync", e)


@router.callback_query(F.data == "menu:batch")
async def cb_menu_batch(callback: CallbackQuery) -> None:
    from bot.handlers.batch_handler import open_batch_from_db_panel

    set_panel(callback.from_user.id, callback.message.chat.id, callback.message.message_id)
    await safe_callback_answer(callback)
    try:
        await open_batch_from_db_panel(callback.message, edit=callback.message)
    except Exception as e:
        await report_callback_failure(callback, "Пакет", e)


@router.callback_query(F.data == "menu:clean")
async def cb_menu_clean(callback: CallbackQuery) -> None:
    from bot.handlers.url_handler import cleanup_user_task_ephemerals

    user_id = callback.from_user.id
    await cleanup_user_task_ephemerals(callback.bot, callback.message.chat.id, user_id)
    deleted = await cleanup_user_chat(callback.bot, callback.message.chat.id, user_id)
    set_panel(user_id, callback.message.chat.id, callback.message.message_id)
    text = (
        f"🧹 Удалено сообщений бота: {deleted}."
        if deleted
        else "🧹 Нет отслеживаемых сообщений.\nСтарые — только вручную в Telegram."
    )
    await safe_edit_text(callback.message, text, reply_markup=get_back_home_keyboard(), parse_mode=None)
    await callback.answer()


def _whisper_model_by_index(idx: int) -> str | None:
    models = list(get_available_models().keys())
    if 0 <= idx < len(models):
        return models[idx]
    return None


def _llm_model_by_index(idx: int) -> str | None:
    models = list(llm_router.get_available_models().keys())
    if 0 <= idx < len(models):
        return models[idx]
    return None


def _apply_settings_action(action: str, parts: list[str], user_id: int) -> tuple[user_settings.UserSettings, str]:
    """Применяет действие настроек. Бросает ValueError при неверных данных."""
    if action == "main":
        return user_settings.get_user_settings(user_id), "Настройки"
    if action == "toggle_transcript":
        s = user_settings.toggle_transcript_in_chat(user_id)
        return s, "Транскрипт в чате"
    if action == "toggle_llm":
        s = user_settings.toggle_llm_in_chat(user_id)
        return s, "AI-ответ в чате"
    if action == "cycle_lang":
        s = user_settings.cycle_transcribe_language(user_id)
        return s, f"Язык: {user_settings.language_label(s.transcribe_language)}"
    if action == "reset":
        return user_settings.reset_to_defaults(user_id), "Сброшено к defaults"
    if action == "w" and len(parts) >= 3:
        model_id = _whisper_model_by_index(int(parts[2]))
        if not model_id:
            raise ValueError("unknown_whisper")
        s = user_settings.set_whisper_model(user_id, model_id)
        return s, user_settings.whisper_label(model_id)
    if action == "l" and len(parts) >= 3:
        model_id = _llm_model_by_index(int(parts[2]))
        if not model_id:
            raise ValueError("unknown_llm")
        s = user_settings.set_llm_model(user_id, model_id)
        return s, user_settings.llm_label(model_id)
    raise ValueError(f"unknown_action:{action}")


async def _render_settings_screen(target, settings: user_settings.UserSettings) -> bool:
    text, markup = render_settings_message(settings)
    return await safe_edit_text(target, text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data.startswith("settings:"))
async def cb_settings(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    user_id = callback.from_user.id
    set_panel(user_id, callback.message.chat.id, callback.message.message_id)
    parts = callback.data.split(":")
    action = parts[1]

    if action == "pick_whisper":
        settings = user_settings.get_user_settings(user_id)
        text, markup = render_whisper_picker(settings)
        ok = await safe_edit_text(callback.message, text, reply_markup=markup, parse_mode="HTML")
        await safe_callback_answer(
            callback,
            "" if ok else "Не удалось обновить экран",
            show_alert=not ok,
        )
        return
    if action == "pick_llm":
        settings = user_settings.get_user_settings(user_id)
        text, markup = render_llm_picker(settings)
        ok = await safe_edit_text(callback.message, text, reply_markup=markup, parse_mode="HTML")
        await safe_callback_answer(
            callback,
            "" if ok else "Не удалось обновить экран",
            show_alert=not ok,
        )
        return
    if action == "close":
        await show_home_on(callback.message, user_id, callback.from_user.first_name or "друг")
        await safe_callback_answer(callback)
        return

    snapshot = user_settings.get_user_settings(user_id)
    try:
        settings, note = _apply_settings_action(action, parts, user_id)
    except ValueError as e:
        code = str(e)
        if code == "unknown_whisper":
            await safe_callback_answer(callback, "Неизвестная модель Whisper.", show_alert=True)
        elif code == "unknown_llm":
            await safe_callback_answer(callback, "Неизвестная модель LLM.", show_alert=True)
        else:
            await safe_callback_answer(callback, "Неизвестная настройка.", show_alert=True)
        return
    except Exception as e:
        logger.exception("Settings apply failed user=%s action=%s", user_id, action)
        await safe_callback_answer(callback, "Ошибка сохранения настройки.", show_alert=True)
        return

    ok = await _render_settings_screen(callback.message, settings)
    if not ok:
        user_settings.restore_user_settings(user_id, snapshot)
        await _render_settings_screen(callback.message, snapshot)
        await safe_callback_answer(
            callback,
            "Сеть: не удалось обновить экран. Настройка отменена.",
            show_alert=True,
        )
        return

    await safe_callback_answer(callback, note)


def _history_item_text(entry, state) -> str:
    has_summary = state.has_summary if state else False
    llm_count = state.llm_call_count if state else 0
    status_line = "✅ транскрипт" + (f" • 🧠 {llm_count} LLM" if has_summary else "")
    return format_history_item_header(entry) + f"\n{status_line}"


@router.callback_query(F.data.startswith("hist_list:"))
async def cb_hist_list(callback: CallbackQuery) -> None:
    page = int(callback.data.split(":")[1])
    set_panel(callback.from_user.id, callback.message.chat.id, callback.message.message_id)
    entries = db.list_videos(callback.from_user.id)
    if not entries:
        await safe_edit_text(
            callback.message,
            "История пуста.",
            reply_markup=get_back_home_keyboard(),
            parse_mode=None,
        )
        return
    keyboard = get_history_list_keyboard(entries, page=page)
    await safe_edit_text(
        callback.message,
        f"📜 История ({len(entries)} записей):",
        reply_markup=keyboard,
        parse_mode=None,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("hist_item:"))
async def cb_hist_item(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    video_id = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 0
    entry = db.get_video(video_id)
    if not entry:
        await callback.answer("Запись не найдена.", show_alert=True)
        return
    state = db.get_state(video_id)
    text = _history_item_text(entry, state)
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=get_history_item_keyboard(video_id, page=page),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("hist_gdrive:"))
async def cb_hist_gdrive(callback: CallbackQuery) -> None:
    video_id = callback.data.split(":")[1]
    state = db.get_state(video_id)
    if not state or not state.gdrive_transcript_url:
        await callback.answer("GDrive ссылка недоступна.", show_alert=True)
        return
    await callback.answer(f"☁️ {state.gdrive_transcript_url}", show_alert=True)


@router.callback_query(F.data.startswith("hist_delete:"))
async def cb_hist_delete(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    video_id = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 0
    db.delete_video(video_id)
    entries = db.list_videos(callback.from_user.id)
    if not entries:
        await safe_edit_text(
            callback.message,
            "История пуста.",
            reply_markup=get_back_home_keyboard(),
            parse_mode=None,
        )
    else:
        max_page = max(0, (len(entries) - 1) // 5)
        page = min(page, max_page)
        keyboard = get_history_list_keyboard(entries, page=page)
        await safe_edit_text(
            callback.message,
            f"✅ Удалено. История ({len(entries)} записей):",
            reply_markup=keyboard,
            parse_mode=None,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("hist_back:"))
async def cb_hist_back(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    video_id = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 0
    entry = db.get_video(video_id)
    if not entry:
        await callback.answer("Запись не найдена.", show_alert=True)
        return
    state = db.get_state(video_id)
    text = _history_item_text(entry, state)
    await safe_edit_text(
        callback.message,
        text,
        reply_markup=get_history_item_keyboard(video_id, page=page),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("hist_analyze:"))
async def cb_hist_analyze(callback: CallbackQuery) -> None:
    from bot.handlers.url_handler import _show_analysis_menu
    from services import task_session
    from bot.ui.progress import ProgressReporter
    from core.config import OMNIROUTE_BASE_URL

    parts = callback.data.split(":")
    video_id = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 0
    entry = db.get_video(video_id)
    if not entry:
        await callback.answer("Запись не найдена.", show_alert=True)
        return

    transcript_text = db.get_transcript_text(video_id)
    if not transcript_text:
        await callback.answer("Текст транскрипта недоступен.", show_alert=True)
        return

    fake_task = MediaTask(
        url=f"https://youtu.be/{entry.video_id}",
        title=entry.title,
        channel=entry.channel,
        duration_sec=entry.duration_sec,
        video_id=entry.video_id,
    )
    session = task_session.register_session(
        video_id,
        {
            "url": fake_task.url,
            "task": fake_task,
            "user_id": callback.from_user.id,
            "transcript": transcript_text,
            "ui": {"from_history": True, "hist_video_id": video_id, "hist_page": page},
        },
    )

    await callback.answer()

    cached_variants = db.get_variants(video_id)
    if cached_variants:
        session["variants"] = cached_variants
        await _show_analysis_menu(callback.message, video_id, session)
        return

    prefs = user_settings.get_user_settings(callback.from_user.id)
    prog = ProgressReporter(callback.message, title=f"🎯 {entry.title}")
    await prog.start()
    prog.set_stage(
        "AI: варианты",
        f"POST {OMNIROUTE_BASE_URL}/chat/completions",
        subdetail=f"Модель: {prefs.llm_model}",
        stage_key=timing_stats.STAGE_LLM_VARIANTS,
        context={"transcript_chars": len(transcript_text)},
    )
    await prog.push()
    try:
        variants = await llm_router.get_analysis_variants(
            transcript_text,
            model=prefs.llm_model,
        )
    except Exception as e:
        logger.exception("hist_analyze error: %s", e)
        err_kb = get_error_keyboard(
            retry_callback=f"retry:llm_variants:{video_id}",
            back_callback=f"hist_back:{video_id}:{page}",
        )
        await prog.show_error(
            "AI: варианты",
            "Ошибка при генерации вариантов.",
            str(e),
            reply_markup=err_kb,
        )
        return
    finally:
        await prog.stop()

    db.save_variants(video_id, variants)
    session["variants"] = variants
    await _show_analysis_menu(callback.message, video_id, session)


_LEGACY_REPLY = frozenset({
    "📥 Новая ссылка",
    "📜 История",
    "📦 Пакет",
    "⚙️ Настройки",
    "☁️ GDrive sync",
    "🧹 Очистить чат",
    "ℹ️ Помощь",
})


@router.message(F.text.in_(_LEGACY_REPLY))
async def legacy_reply_buttons(message: Message) -> None:
    """Старые reply-кнопки: без лишних пузырей — обновляем панель."""
    if not message.from_user:
        return
    await _remove_reply_keyboard(message)
    await delete_message_safe(message.bot, message.chat.id, message.message_id)
    user_id = message.from_user.id
    coords = get_panel(user_id)
    if not coords:
        user_name = message.from_user.first_name or "друг"
        sent = await message.answer(
            screens.home_text(user_name),
            reply_markup=get_home_keyboard(),
            parse_mode="HTML",
        )
        set_panel(user_id, sent.chat.id, sent.message_id)
        coords = (sent.chat.id, sent.message_id)

    chat_id, msg_id = coords
    bot = message.bot
    text_key = message.text or ""

    async def edit_panel(**kwargs):
        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, **kwargs)

    if text_key == "📥 Новая ссылка":
        await edit_panel(text=screens.new_link_prompt_text(), reply_markup=get_back_home_keyboard(), parse_mode="HTML")
    elif text_key == "📜 История":
        entries = db.list_videos(user_id)
        if not entries:
            await edit_panel(text="📜 История пуста.", reply_markup=get_back_home_keyboard(), parse_mode=None)
        else:
            await edit_panel(
                text=f"📜 История ({len(entries)} записей):",
                reply_markup=get_history_list_keyboard(entries, page=0),
                parse_mode=None,
            )
    elif text_key == "⚙️ Настройки":
        settings = user_settings.get_user_settings(user_id)
        t, markup = render_settings_message(settings)
        await edit_panel(text=t, reply_markup=markup, parse_mode="HTML")
    elif text_key == "☁️ GDrive sync":
        from bot.handlers.gdrive_sync_handler import run_gdrive_sync_panel
        from bot.ui.nav import EditTarget

        await run_gdrive_sync_panel(EditTarget(bot, chat_id, msg_id), user_id)
    elif text_key == "📦 Пакет":
        from bot.handlers.batch_handler import open_batch_from_db_panel
        from bot.ui.nav import EditTarget

        panel_msg = EditTarget(bot, chat_id, msg_id)
        await open_batch_from_db_panel(message, edit=panel_msg)  # type: ignore[arg-type]
    elif text_key == "🧹 Очистить чат":
        from bot.handlers.url_handler import cleanup_user_task_ephemerals

        await cleanup_user_task_ephemerals(bot, chat_id, user_id)
        deleted = await cleanup_user_chat(bot, chat_id, user_id)
        body = (
            f"🧹 Удалено сообщений бота: {deleted}."
            if deleted
            else "🧹 Нет отслеживаемых сообщений."
        )
        await edit_panel(text=body, reply_markup=get_back_home_keyboard(), parse_mode=None)
    elif text_key == "ℹ️ Помощь":
        await edit_panel(text=screens.help_text(), reply_markup=get_back_home_keyboard(), parse_mode="HTML")


@router.message(Command("search"))
async def cmd_search(message: Message) -> None:
    from core.config import ENABLE_KB, KB_API_URL

    if not ENABLE_KB or not KB_API_URL:
        await message.answer("База знаний отключена.", parse_mode=None)
        return
    await message.answer("KB поиск не реализован.", parse_mode=None)

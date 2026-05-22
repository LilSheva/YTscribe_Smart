"""
bot/handlers/start.py — Обработчики команд /start, /help и UI истории.
"""

import logging

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery

from core.config import features
from bot.keyboards.main_menu import get_main_reply_keyboard, get_history_list_keyboard, get_history_item_keyboard
from services import history

router = Router(name="start")
logger = logging.getLogger(__name__)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user_name = message.from_user.first_name if message.from_user else "друг"
    await message.answer(
        f"👋 Привет, {user_name}! Отправь ссылку на YouTube-видео или выбери действие.",
        reply_markup=get_main_reply_keyboard(),
    )


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: Message) -> None:
    lines = ["<b>YTS Bot</b> — транскрибация и AI-анализ YouTube-видео.\n"]
    if features.downloader:
        lines.append("• Отправь ссылку → скачать M4A/MP4")
    if features.transcript:
        lines.append("• Транскрибировать речь в текст (Groq Whisper)")
    if features.llm:
        lines.append("• AI-анализ: саммари, инсайты и другие режимы")
    lines.append("\n📜 История — последние транскрипты")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(F.text == "📥 Новая ссылка")
async def btn_new_link(message: Message) -> None:
    await message.answer("Отправь ссылку на YouTube-видео:")


@router.message(F.text == "📜 История")
async def btn_history(message: Message) -> None:
    entries = history.list_by_user(message.from_user.id)
    if not entries:
        await message.answer("История пуста. Транскрибируй видео — оно появится здесь.")
        return
    keyboard = get_history_list_keyboard(entries, page=0)
    await message.answer(f"📜 История ({len(entries)} записей):", reply_markup=keyboard)


@router.callback_query(F.data.startswith("hist_list:"))
async def cb_hist_list(callback: CallbackQuery) -> None:
    page = int(callback.data.split(":")[1])
    entries = history.list_by_user(callback.from_user.id)
    if not entries:
        await callback.message.edit_text("История пуста.")
        return
    keyboard = get_history_list_keyboard(entries, page=page)
    await callback.message.edit_text(f"📜 История ({len(entries)} записей):", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("hist_item:"))
async def cb_hist_item(callback: CallbackQuery) -> None:
    entry_id = int(callback.data.split(":")[1])
    entry = history.get(entry_id)
    if not entry:
        await callback.answer("Запись не найдена.", show_alert=True)
        return
    mins = entry.duration_sec // 60
    text = f"🎬 <b>{entry.title}</b>\n📺 {entry.channel} • {mins} мин\n🕐 {entry.created_at[:16]}"
    await callback.message.edit_text(text, reply_markup=get_history_item_keyboard(entry_id), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("hist_gdrive:"))
async def cb_hist_gdrive(callback: CallbackQuery) -> None:
    entry_id = int(callback.data.split(":")[1])
    entry = history.get(entry_id)
    if not entry:
        await callback.answer("Запись не найдена.", show_alert=True)
        return
    await callback.answer(f"☁️ {entry.gdrive_url}", show_alert=True)


@router.callback_query(F.data.startswith("hist_delete:"))
async def cb_hist_delete(callback: CallbackQuery) -> None:
    entry_id = int(callback.data.split(":")[1])
    history.delete(entry_id)
    entries = history.list_by_user(callback.from_user.id)
    if not entries:
        await callback.message.edit_text("История пуста.")
    else:
        keyboard = get_history_list_keyboard(entries, page=0)
        await callback.message.edit_text(f"✅ Удалено. История ({len(entries)} записей):", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("hist_analyze:"))
async def cb_hist_analyze(callback: CallbackQuery) -> None:
    from bot.handlers.url_handler import _tasks
    entry_id = int(callback.data.split(":")[1])
    entry = history.get(entry_id)
    if not entry:
        await callback.answer("Запись не найдена.", show_alert=True)
        return

    transcript_text = history.get_transcript_text(entry)
    if not transcript_text:
        await callback.answer("Текст транскрипта недоступен.", show_alert=True)
        return

    import uuid
    from core.models import MediaTask
    task_id = uuid.uuid4().hex[:8]
    fake_task = MediaTask(
        url=f"https://youtu.be/{entry.video_id}",
        title=entry.title,
        channel=entry.channel,
        duration_sec=entry.duration_sec,
        video_id=entry.video_id,
    )
    _tasks[task_id] = {
        "url": fake_task.url,
        "task": fake_task,
        "user_id": callback.from_user.id,
        "transcript": transcript_text,
    }

    await callback.answer()
    from services import llm_router
    from bot.keyboards.main_menu import get_analysis_variants_keyboard
    await callback.message.edit_text("🤔 Анализирую содержание...", parse_mode=None)
    variants = await llm_router.get_analysis_variants(transcript_text)
    _tasks[task_id]["variants"] = variants
    keyboard = get_analysis_variants_keyboard(task_id, variants)
    await callback.message.edit_text(
        f"🎯 <b>{entry.title}</b>\n\nВыберите тип анализа:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.message(Command("search"))
async def cmd_search(message: Message) -> None:
    from core.config import ENABLE_KB, KB_API_URL
    if not ENABLE_KB or not KB_API_URL:
        await message.answer("База знаний отключена.", parse_mode=None)
        return
    await message.answer("KB поиск не реализован.", parse_mode=None)

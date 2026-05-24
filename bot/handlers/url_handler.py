"""
bot/handlers/url_handler.py — Обработка YouTube-ссылок и callback-действий.

Поток:
  1. Пользователь отправляет ссылку → get_info() → карточка + клавиатура.
  2. Нажатие кнопки → скачивание → GDrive upload → ссылка юзеру.
  3. Если файл <= 50MB → кнопка "Получить в чат".
"""

import uuid
import logging
from pathlib import Path

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from core.config import (
    features,
    ENABLE_GDRIVE,
    OMNIROUTE_BASE_URL,
)
from core.models import MediaTask
from core.exceptions import ServiceDisabledError, DownloadError, TranscriptionError, LLMError, YTSBotError
from services import db
from services import downloader
from services import gdrive
from services import llm_router
from services import timing_stats
from services import user_settings
from services.llm_persist import persist_llm_result
from services.ai_chat import run_analysis_chat, get_chat_turn_count
from services import task_session
from services.transcript_paths import find_transcript_file
from bot.pipeline.media import download_audio_with_progress, download_with_progress, transcribe_with_progress
from bot.pipeline.transcript import save_transcript
from bot.utils.chat_delivery import send_document_file, send_text_parts, send_export_hint
from utils.url_parser import YOUTUBE_URL_PATTERN, extract_video_id, normalize_youtube_url
from bot.keyboards.main_menu import (
    get_media_keyboard,
    get_post_download_keyboard,
    get_analysis_menu_keyboard,
    get_ai_result_keyboard,
    get_fsm_cancel_keyboard,
    get_ai_chat_keyboard,
    get_error_keyboard,
    MAX_PAST_RESULTS,
)
from bot.ui.progress import ProgressReporter
from bot.ui.handler_fail import report_callback_failure, report_ui_failure
from bot.ui.panel import get_panel, set_panel, show_home
from bot.ui.telegram_safe import safe_edit_text
from bot.ui.nav import cleanup_ephemeral, track_ephemeral, delete_message_safe, EditTarget
from bot.ui.message_registry import register_bot_message, cleanup_user_chat
from bot.ui import screens
from utils.telegram_format import bold_html, escape_html

class VariantRefreshState(StatesGroup):
    waiting_comment = State()


class AnalysisChatState(StatesGroup):
    waiting_message = State()


router = Router(name="url_handler")
logger = logging.getLogger(__name__)

_url_retries: dict[str, str] = {}

TELEGRAM_FILE_LIMIT_MB: float = 50.0


def _generate_retry_id() -> str:
    """Короткий ID для retry-callback без URL в callback_data."""
    return uuid.uuid4().hex[:8]


def _extract_url(text: str) -> str | None:
    """Извлекает первый YouTube URL из текста."""
    match = YOUTUBE_URL_PATTERN.search(text)
    if not match:
        return None
    return normalize_youtube_url(match.group(0))


def _load_transcript_for_task(task_data: dict) -> str | None:
    """Подтягивает транскрипт из памяти задачи или из БД."""
    return task_session.load_transcript(task_data)


async def cleanup_user_task_ephemerals(bot, chat_id: int, user_id: int) -> None:
    for task_data in task_session.sessions_for_user(user_id):
        await cleanup_ephemeral(bot, chat_id, task_data)


async def _show_video_card(message: Message, video_id: str, task_data: dict) -> None:
    task: MediaTask = task_data["task"]
    has_transcript = bool(_load_transcript_for_task(task_data))
    await safe_edit_text(
        message,
        screens.video_card_text(task, has_transcript=has_transcript),
        reply_markup=get_media_keyboard(video_id, has_transcript=has_transcript),
        parse_mode="HTML",
    )


async def _show_actions(message: Message, video_id: str, task_data: dict, *, cached: bool = False) -> None:
    task: MediaTask = task_data["task"]
    text = _load_transcript_for_task(task_data) or ""
    user_id = task_data.get("user_id") or message.chat.id
    chat_settings = user_settings.get_chat_output_settings(user_id)
    await safe_edit_text(
        message,
        screens.transcript_done_text(
            task,
            text,
            cached=cached,
            show_preview=chat_settings.show_transcript_in_chat,
        ),
        reply_markup=get_media_keyboard(video_id, has_transcript=True),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def _show_analysis_menu(message: Message, video_id: str, task_data: dict) -> None:
    task: MediaTask = task_data["task"]
    variants = task_data.get("variants") or db.get_variants(task.video_id) or []
    task_data["variants"] = variants
    past_results = [vars(r) for r in db.get_analysis_results(task.video_id)]
    past_total = len(past_results)
    past_shown = min(past_total, MAX_PAST_RESULTS)
    ui = task_data.get("ui", {})
    back_cb = f"hist_back:{ui['hist_video_id']}:{ui.get('hist_page', 0)}" if ui.get("from_history") else None
    await safe_edit_text(
        message,
        screens.analysis_menu_text(task, past_shown=past_shown, past_total=past_total),
        reply_markup=get_analysis_menu_keyboard(
            video_id, variants, past_results, back_callback=back_cb
        ),
        parse_mode="HTML",
    )


async def _ensure_transcript(
    callback: CallbackQuery,
    task_data: dict,
    *,
    video_id: str | None = None,
) -> str:
    """Возвращает текст транскрипта: из кэша/БД или через скачивание+транскрибацию."""
    text = _load_transcript_for_task(task_data)
    if text:
        return text

    task: MediaTask = task_data["task"]
    vid = video_id or task.video_id
    url = task_data["url"]
    prefix_dl = f"⬇️ Скачиваю аудио {bold_html(task.title)}"
    file_path = await download_audio_with_progress(
        callback.message, url, task, prefix_dl, video_id=vid
    )
    task_data["file_path"] = file_path

    text = await transcribe_with_progress(
        callback.message,
        task,
        file_path,
        callback.from_user.id,
        video_id=vid,
    )
    await save_transcript(task_data, text, callback.message)
    return text


async def _fetch_metadata_on_message(
    message: Message,
    url: str,
    user_id: int,
) -> MediaTask | None:
    """Повторная загрузка метаданных в то же сообщение."""
    prog = ProgressReporter(message, title="🎬 Новое видео")
    await prog.start()
    prog.set_stage(
        "Метаданные",
        "yt-dlp --dump-json",
        stage_key=timing_stats.STAGE_METADATA,
    )
    await prog.push()
    retry_id = _generate_retry_id()
    _url_retries[retry_id] = url
    err_kb = get_error_keyboard(retry_callback=f"retry:info:{retry_id}", close=True)
    try:
        task = await downloader.get_info(url)
    except (ServiceDisabledError, DownloadError) as e:
        await prog.show_error("Метаданные", e.message, reply_markup=err_kb)
        return None
    except Exception as e:
        logger.error(f"Metadata retry error: {e}")
        await prog.show_error(
            "Метаданные",
            "Не удалось получить информацию о видео.",
            str(e),
            reply_markup=err_kb,
        )
        return None
    finally:
        await prog.stop()
    return task


async def _show_card_for_url(message: Message, url: str, user_id: int, task: MediaTask) -> str:
    """Сохраняет задачу и показывает карточку видео. Возвращает video_id."""
    db.upsert_video(task, user_id)
    video_id = task.video_id
    cached_text = db.get_transcript_text(video_id)
    session = task_session.register_session(
        video_id,
        {"url": url, "task": task, "user_id": user_id},
    )
    if cached_text:
        session["transcript"] = cached_text
    await message.edit_text(
        screens.video_card_text(
            task,
            has_transcript=bool(cached_text),
            from_cache=False,
        ),
        reply_markup=get_media_keyboard(video_id, has_transcript=bool(cached_text)),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    return video_id


@router.callback_query(F.data.startswith("retry:"))
async def cb_retry(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    op = parts[1]

    if op == "info" and len(parts) >= 3:
        url = _url_retries.pop(parts[2], None)
        if not url:
            await callback.answer("Ссылка устарела. Отправьте снова.", show_alert=True)
            return
        await callback.answer("Повтор…")
        task = await _fetch_metadata_on_message(callback.message, url, callback.from_user.id)
        if task:
            await _show_card_for_url(callback.message, url, callback.from_user.id, task)
        return

    await callback.answer("Повтор…")

    if op == "dl" and len(parts) >= 4:
        video_id, fmt = parts[2], parts[3]
        await _run_download(callback.message, video_id, fmt, callback.from_user.id)
        return

    if op == "transcript" and len(parts) >= 3:
        video_id = parts[2]
        task_data = task_session.require_session(video_id, callback.from_user.id)
        if not task_data:
            await callback.answer("Видео не найдено.", show_alert=True)
            return
        try:
            text = await _ensure_transcript(callback, task_data, video_id=video_id)
        except (ServiceDisabledError, DownloadError, TranscriptionError):
            return
        except Exception as e:
            await report_callback_failure(callback, "Транскрибация", e)
            return
        await _show_actions(callback.message, video_id, task_data)
        logger.info(f"Transcript retry complete: {len(text)} chars")
        return

    if op == "llm_variants" and len(parts) >= 3:
        video_id = parts[2]
        await _run_llm_variants(callback.message, video_id, callback.from_user.id)
        return

    if op == "analyze" and len(parts) >= 4:
        video_id, slot_idx = parts[2], int(parts[3])
        await _run_llm_analyze(callback.message, video_id, slot_idx, callback.from_user.id)
        return

    await callback.answer("Неизвестное действие.", show_alert=True)


@router.callback_query(F.data.startswith("nav:"))
async def cb_nav(callback: CallbackQuery) -> None:
    """Inline-навигация: card / actions / analysis."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        return
    action, video_id = parts[1], parts[2]
    task_data = task_session.require_session(video_id, callback.from_user.id)
    if not task_data:
        await callback.answer("Видео не найдено. Отправьте ссылку заново.", show_alert=True)
        return

    await callback.answer()
    await cleanup_ephemeral(callback.bot, callback.message.chat.id, task_data)

    if action == "card":
        await _show_video_card(callback.message, video_id, task_data)
    elif action in ("actions", "transcript"):
        await _show_actions(callback.message, video_id, task_data)
    elif action == "analysis":
        await _run_llm_variants(callback.message, video_id, callback.from_user.id)


@router.callback_query(F.data.startswith("ui:close_task:"))
async def cb_close_task(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    video_id = parts[2] if len(parts) > 2 else ""
    task_data = task_session.get_session(video_id) if video_id else None
    if task_data:
        await cleanup_ephemeral(callback.bot, callback.message.chat.id, task_data)
    name = callback.from_user.first_name or "друг"
    await show_home(
        callback.bot,
        callback.from_user.id,
        callback.message.chat.id,
        callback.message.message_id,
        name,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ui:hide_export:"))
async def cb_hide_export(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    video_id = parts[2] if len(parts) > 2 else ""
    task_data = task_session.get_session(video_id) if video_id else None
    if task_data:
        await cleanup_ephemeral(callback.bot, callback.message.chat.id, task_data)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("Скрыто")


@router.callback_query(F.data.startswith("fsm_cancel:"))
async def cb_fsm_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    video_id = callback.data.split(":")[1]
    await state.clear()
    task_data = task_session.require_session(video_id, callback.from_user.id)
    if not task_data:
        await callback.answer("Видео не найдено.", show_alert=True)
        return
    await callback.answer("Отменено")
    await _show_analysis_menu(callback.message, video_id, task_data)


@router.message(VariantRefreshState.waiting_comment, F.text == "/cancel")
async def cb_variants_cancel_cmd(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    video_id = data.get("refresh_video_id")
    await state.clear()
    await delete_message_safe(message.bot, message.chat.id, message.message_id)
    task_data = task_session.require_session(video_id, message.from_user.id) if video_id else None
    if not task_data:
        await message.answer("Отменено.")
        return
    chat_id = data.get("refresh_chat_id")
    msg_id = data.get("refresh_msg_id")
    if chat_id and msg_id:
        variants = task_data.get("variants") or []
        past = [vars(r) for r in db.get_analysis_results(task_data["task"].video_id)]
        ui = task_data.get("ui", {})
        back_cb = f"hist_back:{ui['hist_video_id']}" if ui.get("from_history") else None
        await message.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=screens.analysis_menu_text(task_data["task"]),
            reply_markup=get_analysis_menu_keyboard(
                video_id, variants, past, back_callback=back_cb
            ),
            parse_mode="HTML",
        )


@router.message(F.text.regexp(YOUTUBE_URL_PATTERN))
async def handle_youtube_url(message: Message) -> None:
    """Обработка входящей YouTube-ссылки: парсинг метаданных + клавиатура."""
    url = _extract_url(message.text or "")
    if not url:
        return

    # Убедимся что URL полный
    if not url.startswith("http"):
        url = "https://" + url.lstrip("/")
    url = normalize_youtube_url(url) or url

    logger.info(f"Получена ссылка от {message.from_user.id}: {url}")

    user_id = message.from_user.id
    panel = get_panel(user_id)
    if panel:
        chat_id, msg_id = panel
        await delete_message_safe(message.bot, message.chat.id, message.message_id)
        status_msg: Message | EditTarget = EditTarget(message.bot, chat_id, msg_id)
    else:
        sent = await message.answer("⏳ Запуск...")
        set_panel(user_id, sent.chat.id, sent.message_id)
        status_msg = sent

    video_id = extract_video_id(url)
    cached_entry = db.has_cached_metadata(video_id) if video_id else None
    from_cache = cached_entry is not None

    if from_cache:
        task = db.video_entry_to_task(cached_entry, url)
        logger.info(f"Метаданные из БД: {task.video_id}")
    else:
        prog = ProgressReporter(status_msg, title="🎬 Новое видео")
        await prog.start()
        prog.set_stage(
            "Метаданные",
            "yt-dlp --dump-json",
            stage_key=timing_stats.STAGE_METADATA,
        )
        await prog.push()

        try:
            task = await downloader.get_info(url)
        except ServiceDisabledError as e:
            retry_id = _generate_retry_id()
            _url_retries[retry_id] = url
            err_kb = get_error_keyboard(
                retry_callback=f"retry:info:{retry_id}",
                close=True,
            )
            await prog.show_error("Метаданные", e.message, reply_markup=err_kb)
            return
        except DownloadError as e:
            retry_id = _generate_retry_id()
            _url_retries[retry_id] = url
            err_kb = get_error_keyboard(
                retry_callback=f"retry:info:{retry_id}",
                close=True,
            )
            await prog.show_error("Метаданные", e.message, reply_markup=err_kb)
            return
        except Exception as e:
            logger.error(f"Unexpected error in get_info: {e}")
            retry_id = _generate_retry_id()
            _url_retries[retry_id] = url
            err_kb = get_error_keyboard(
                retry_callback=f"retry:info:{retry_id}",
                close=True,
            )
            await prog.show_error(
                "Метаданные",
                "Не удалось получить информацию о видео.",
                str(e),
                reply_markup=err_kb,
            )
            return
        finally:
            await prog.stop()

    db.upsert_video(task, message.from_user.id)
    video_id = task.video_id
    cached_text = db.get_transcript_text(video_id)

    session = task_session.register_session(
        video_id,
        {"url": url, "task": task, "user_id": message.from_user.id},
    )
    if cached_text:
        session["transcript"] = cached_text

    try:
        await status_msg.edit_text(
            screens.video_card_text(
                task,
                has_transcript=bool(cached_text),
                from_cache=from_cache,
            ),
            reply_markup=get_media_keyboard(video_id, has_transcript=bool(cached_text)),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("Не удалось показать карточку видео: %s", e)
        await status_msg.edit_text(
            "❌ Не удалось показать карточку видео. Попробуйте ещё раз.",
            parse_mode=None,
        )


@router.callback_query(F.data.startswith("dl_m4a:"))
async def cb_download_m4a(callback: CallbackQuery) -> None:
    """Callback: скачивание M4A → GDrive → ссылка."""
    await _handle_download(callback, format_type="m4a")


@router.callback_query(F.data.startswith("dl_mp4:"))
async def cb_download_mp4(callback: CallbackQuery) -> None:
    """Callback: скачивание MP4 → GDrive → ссылка."""
    await _handle_download(callback, format_type="mp4")


@router.callback_query(F.data.startswith("send_chat:"))
async def cb_send_to_chat(callback: CallbackQuery) -> None:
    """Callback: отправка файла в чат (если <= 50MB)."""
    video_id = callback.data.split(":")[1]
    task_data = task_session.require_session(video_id, callback.from_user.id)

    if not task_data or "file_path" not in task_data:
        await callback.answer("❌ Файл не найден или уже удалён.", show_alert=True)
        return

    file_path: Path = task_data["file_path"]
    if not file_path.exists():
        await callback.answer("❌ Файл уже удалён с диска.", show_alert=True)
        return

    size_mb = file_path.stat().st_size / (1024 * 1024)
    if size_mb > TELEGRAM_FILE_LIMIT_MB:
        await callback.answer(
            f"⚠️ Файл слишком большой ({size_mb:.1f} MB > {TELEGRAM_FILE_LIMIT_MB} MB)",
            show_alert=True,
        )
        return

    await callback.answer("📤 Отправляю файл...")
    user_id = callback.from_user.id
    await cleanup_ephemeral(callback.bot, callback.message.chat.id, task_data)

    try:
        document = FSInputFile(file_path)
        sent = await callback.message.answer_document(
            document=document,
            caption=f"📁 {file_path.name}",
        )
        track_ephemeral(task_data, sent.message_id)
        register_bot_message(user_id, sent.message_id)
        await send_export_hint(
            callback.bot,
            callback.message.chat.id,
            video_id,
            task_data=task_data,
            user_id=user_id,
        )
        logger.info(f"Файл отправлен в чат: {file_path.name} ({size_mb:.1f} MB)")
    except Exception as e:
        logger.error(f"Ошибка отправки файла в чат: {e}")
        err = await callback.message.answer(f"❌ Не удалось отправить файл: {e}")
        track_ephemeral(task_data, err.message_id)
        register_bot_message(user_id, err.message_id)


async def _run_download(message: Message, video_id: str, format_type: str, user_id: int) -> None:
    task_data = task_session.require_session(video_id, user_id)
    if not task_data:
        return

    url = task_data["url"]
    task: MediaTask = task_data["task"]

    try:
        file_path = await download_with_progress(
            message,
            url,
            task,
            format_type,
            title=f"⬇️ {task.title} ({format_type.upper()})",
            video_id=video_id,
        )
    except (ServiceDisabledError, DownloadError):
        return
    except Exception as e:
        logger.exception("Download failed video=%s", video_id)
        await report_ui_failure(message, "Скачивание", technical=str(e)[:200])
        return

    size_mb = file_path.stat().st_size / (1024 * 1024)
    task_data["file_path"] = file_path

    gdrive_url: str | None = None
    gdrive_error: str = ""
    if ENABLE_GDRIVE:
        prog = ProgressReporter(message, title=f"⬇️ {task.title}")
        await prog.start()
        prog.set_stage(
            "Google Drive",
            f"Загрузка {format_type.upper()} ({size_mb:.1f} MB)",
            stage_key=timing_stats.STAGE_GDRIVE_MEDIA,
            context={"file_size_mb": round(size_mb, 1)},
        )
        await prog.push()
        try:
            gdrive_result = await gdrive.upload(file_path)
            gdrive_url = gdrive_result.public_url
        except (ServiceDisabledError, YTSBotError) as e:
            gdrive_error = e.message
            logger.warning(f"GDrive upload failed: {e.message}")
        except Exception as e:
            gdrive_error = str(e)
            logger.error(f"GDrive unexpected error: {e}")
        await prog.stop()

    can_send = size_mb <= TELEGRAM_FILE_LIMIT_MB
    keyboard = get_post_download_keyboard(video_id, can_send_to_chat=can_send)

    await message.edit_text(
        screens.download_done_text(
            task,
            format_type=format_type,
            size_mb=size_mb,
            gdrive_url=gdrive_url,
            gdrive_error=gdrive_error,
            can_send=can_send,
            file_limit_mb=TELEGRAM_FILE_LIMIT_MB,
            enable_gdrive=ENABLE_GDRIVE,
        ),
        reply_markup=keyboard,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

    logger.info(
        f"Download complete: {file_path.name} ({size_mb:.1f} MB), "
        f"GDrive: {'OK' if gdrive_url else 'SKIP'}, "
        f"Chat send: {'available' if can_send else 'too large'}"
    )


async def _run_llm_variants(message: Message, video_id: str, user_id: int) -> None:
    task_data = task_session.require_session(video_id, user_id)
    if not task_data:
        return

    task: MediaTask = task_data["task"]
    text = _load_transcript_for_task(task_data)
    if not text:
        return

    cached_variants = db.get_variants(task.video_id)
    if cached_variants:
        task_data["variants"] = cached_variants
        await _show_analysis_menu(message, video_id, task_data)
        return

    prefs = user_settings.get_user_settings(user_id)
    err_kb = get_error_keyboard(
        retry_callback=f"retry:llm_variants:{video_id}",
        back_callback=f"nav:transcript:{video_id}",
    )
    prog = ProgressReporter(message, title=f"🎯 {task.title}")
    await prog.start()
    prog.set_stage(
        "AI: варианты",
        f"POST {OMNIROUTE_BASE_URL}/chat/completions",
        subdetail=f"Модель: {prefs.llm_model}",
        stage_key=timing_stats.STAGE_LLM_VARIANTS,
        context={"transcript_chars": len(text)},
    )
    await prog.push()
    try:
        variants = await llm_router.get_analysis_variants(text, model=prefs.llm_model)
    except (ServiceDisabledError, LLMError) as e:
        await prog.show_error("AI: варианты", e.message, reply_markup=err_kb)
        return
    except Exception as e:
        logger.error(f"LLM variants error: {e}")
        await prog.show_error(
            "AI: варианты",
            "Ошибка при генерации вариантов.",
            str(e),
            reply_markup=err_kb,
        )
        return
    finally:
        await prog.stop()

    db.save_variants(task.video_id, variants)
    task_data["variants"] = variants
    await _show_analysis_menu(message, video_id, task_data)


async def _run_llm_analyze(message: Message, video_id: str, slot_idx: int, user_id: int) -> None:
    task_data = task_session.require_session(video_id, user_id)
    if not task_data:
        return

    task: MediaTask = task_data["task"]
    text = _load_transcript_for_task(task_data) or ""
    if not text.strip():
        return

    variants: list[dict] = task_data.get(
        "variants",
        [{"idx": 1, "label": "Саммари", "prompt": "Сделай структурированное саммари видео."}],
    )
    chosen = next((v for v in variants if v["idx"] == slot_idx), variants[0])

    prefs = user_settings.get_user_settings(user_id)
    err_kb = get_error_keyboard(
        retry_callback=f"retry:analyze:{video_id}:{slot_idx}",
        back_callback=f"nav:analysis:{video_id}",
    )
    prog = ProgressReporter(message, title=f"🧠 {chosen['label']}: {task.title}")
    await prog.start()
    prog.set_stage(
        "AI-анализ",
        f"POST {OMNIROUTE_BASE_URL}/chat/completions",
        subdetail=f"Модель: {prefs.llm_model} • {len(text)} симв. транскрипта",
        stage_key=timing_stats.STAGE_LLM_ANALYZE,
        context={"transcript_chars": len(text)},
    )
    await prog.push()
    try:
        result = await llm_router.analyze(
            text,
            user_prompt=chosen["prompt"],
            model=prefs.llm_model,
        )
    except (ServiceDisabledError, LLMError) as e:
        await prog.show_error("AI-анализ", e.message, reply_markup=err_kb)
        return
    except Exception as e:
        logger.error(f"LLM analyze error: {e}")
        await prog.show_error(
            "AI-анализ",
            "Ошибка при AI-анализе.",
            str(e),
            reply_markup=err_kb,
        )
        return
    finally:
        await prog.stop()

    gdrive_md_url: str | None = None
    result_id: int | None = None

    try:
        result_id = await persist_llm_result(
            task.video_id,
            chosen["label"],
            chosen["prompt"],
            result,
            model=prefs.llm_model,
        )
        state = db.get_state(task.video_id)
        if state and state.gdrive_transcript_url:
            gdrive_md_url = state.gdrive_transcript_url
    except Exception as e:
        logger.warning(f"DB update error after LLM: {e}")

    chat_settings = user_settings.get_chat_output_settings(user_id)
    await message.edit_text(
        screens.llm_result_text(
            task,
            chosen["label"],
            result,
            transcript_len=len(text),
            gdrive_url=gdrive_md_url,
            show_body=chat_settings.show_llm_in_chat,
        ),
        reply_markup=get_ai_result_keyboard(video_id, result_id),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    logger.info(f"LLM analyze complete [{chosen['label']}]: {task.title}")


async def _handle_download(callback: CallbackQuery, format_type: str) -> None:
    video_id = callback.data.split(":")[1]
    task_data = task_session.require_session(video_id, callback.from_user.id)

    if not task_data:
        await callback.answer("❌ Видео не найдено. Отправьте ссылку заново.", show_alert=True)
        return

    await callback.answer()
    await _run_download(callback.message, video_id, format_type, callback.from_user.id)


@router.callback_query(F.data.startswith("transcript:"))
async def cb_transcribe(callback: CallbackQuery) -> None:
    video_id = callback.data.split(":")[1]
    task_data = task_session.require_session(video_id, callback.from_user.id)

    if not task_data:
        await callback.answer("❌ Видео не найдено. Отправьте ссылку заново.", show_alert=True)
        return

    task: MediaTask = task_data["task"]
    await callback.answer()

    cached = _load_transcript_for_task(task_data)
    if cached:
        await _show_actions(callback.message, video_id, task_data, cached=True)
        return

    try:
        text = await _ensure_transcript(callback, task_data, video_id=video_id)
    except (ServiceDisabledError, DownloadError, TranscriptionError):
        return
    except Exception as e:
        await report_callback_failure(callback, "Транскрибация", e)
        return

    await _show_actions(callback.message, video_id, task_data)
    logger.info(f"Transcript complete: {task.title} ({len(text)} chars)")


@router.callback_query(F.data.startswith("llm_variants:"))
async def cb_llm_variants(callback: CallbackQuery) -> None:
    """Показывает варианты анализа после транскрипции."""
    video_id = callback.data.split(":")[1]
    task_data = task_session.require_session(video_id, callback.from_user.id)
    if not task_data:
        await callback.answer("❌ Видео не найдено. Отправьте ссылку заново.", show_alert=True)
        return

    task: MediaTask = task_data["task"]
    await callback.answer()

    text = _load_transcript_for_task(task_data)
    if not text:
        try:
            text = await _ensure_transcript(callback, task_data, video_id=video_id)
        except (ServiceDisabledError, DownloadError, TranscriptionError):
            return
        except Exception as e:
            await report_callback_failure(callback, "Транскрибация", e)
            return

    await _run_llm_variants(callback.message, video_id, callback.from_user.id)


@router.callback_query(F.data.startswith("view_result:"))
async def cb_view_result(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Неверный запрос.", show_alert=True)
        return
    video_id, result_id = parts[1], int(parts[2])
    task_data = task_session.require_session(video_id, callback.from_user.id)
    if not task_data:
        await callback.answer("Видео не найдено.", show_alert=True)
        return

    r = db.get_analysis_result(result_id)
    if not r:
        await callback.answer("Ответ не найден.", show_alert=True)
        return
    await callback.answer()
    chat_settings = user_settings.get_chat_output_settings(callback.from_user.id)
    await callback.message.edit_text(
        screens.view_result_text(
            r.label,
            r.result,
            created_at=r.created_at,
            show_body=chat_settings.show_llm_in_chat,
        ),
        reply_markup=get_ai_result_keyboard(video_id, result_id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("reveal_transcript:"))
async def cb_reveal_transcript(callback: CallbackQuery) -> None:
    video_id = callback.data.split(":")[1]
    task_data = task_session.require_session(video_id, callback.from_user.id)
    if not task_data:
        await callback.answer("Видео не найдено.", show_alert=True)
        return

    text = _load_transcript_for_task(task_data)
    if not text:
        await callback.answer("Транскрипт не найден.", show_alert=True)
        return

    task: MediaTask = task_data["task"]
    user_id = callback.from_user.id
    await callback.answer("Отправляю…")
    await cleanup_ephemeral(callback.bot, callback.message.chat.id, task_data)

    json_path = find_transcript_file(task.video_id)
    if json_path and json_path.suffix.lower() == ".json" and json_path.exists():
        await send_document_file(
            callback.bot,
            callback.message.chat.id,
            json_path,
            caption=f"JSON: {task.title}",
            task_data=task_data,
            user_id=user_id,
        )
    else:
        await send_text_parts(
            callback.bot,
            callback.message.chat.id,
            text,
            header=f"Транскрипт: {task.title}",
            task_data=task_data,
            user_id=user_id,
        )

    await send_export_hint(
        callback.bot,
        callback.message.chat.id,
        video_id,
        task_data=task_data,
        user_id=user_id,
    )


@router.callback_query(F.data.startswith("reveal_llm:"))
async def cb_reveal_llm(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Неверный запрос.", show_alert=True)
        return
    video_id, result_id = parts[1], int(parts[2])
    task_data = task_session.require_session(video_id, callback.from_user.id)
    if not task_data:
        await callback.answer("Видео не найдено.", show_alert=True)
        return

    r = db.get_analysis_result(result_id)
    if not r:
        await callback.answer("Ответ не найден.", show_alert=True)
        return

    user_id = callback.from_user.id
    await callback.answer("Отправляю…")
    await cleanup_ephemeral(callback.bot, callback.message.chat.id, task_data)
    await send_text_parts(
        callback.bot,
        callback.message.chat.id,
        r.result,
        header=f"🧠 {r.label}",
        task_data=task_data,
        user_id=user_id,
    )
    await send_export_hint(
        callback.bot,
        callback.message.chat.id,
        video_id,
        task_data=task_data,
        user_id=user_id,
    )


@router.callback_query(F.data.startswith("variants_refresh:"))
async def cb_variants_refresh(callback: CallbackQuery, state: FSMContext) -> None:
    video_id = callback.data.split(":")[1]
    if not task_session.require_session(video_id, callback.from_user.id):
        await callback.answer("Видео не найдено.", show_alert=True)
        return
    await state.update_data(
        refresh_video_id=video_id,
        refresh_msg_id=callback.message.message_id,
        refresh_chat_id=callback.message.chat.id,
    )
    await callback.answer()
    await state.set_state(VariantRefreshState.waiting_comment)
    await callback.message.edit_text(
        "✏️ <b>Уточнение анализа</b>\n\n"
        "Напишите комментарий одним сообщением\n"
        "<i>или /cancel</i>",
        reply_markup=get_fsm_cancel_keyboard(video_id),
        parse_mode="HTML",
    )


@router.message(VariantRefreshState.waiting_comment)
async def cb_variants_refresh_comment(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    video_id = data.get("refresh_video_id")
    chat_id = data.get("refresh_chat_id")
    msg_id = data.get("refresh_msg_id")
    await state.clear()

    task_data = task_session.require_session(video_id, message.from_user.id) if video_id else None
    if not task_data or not chat_id or not msg_id:
        await message.answer("Видео не найдено. Отправьте ссылку заново.")
        return

    await delete_message_safe(message.bot, message.chat.id, message.message_id)

    task: MediaTask = task_data["task"]
    text: str = _load_transcript_for_task(task_data) or ""
    comment = message.text or ""

    anchor = EditTarget(message.bot, chat_id, msg_id)
    user_id = task_data.get("user_id") or message.from_user.id
    prefs = user_settings.get_user_settings(user_id)
    prog = ProgressReporter(anchor, title=f"🎯 {task.title}")
    await prog.start()
    prog.set_stage(
        "AI: новые варианты",
        f"POST {OMNIROUTE_BASE_URL}/chat/completions",
        subdetail=f"{prefs.llm_model} • {comment[:40]}",
        stage_key=timing_stats.STAGE_LLM_VARIANTS,
        context={"transcript_chars": len(text)},
    )
    await prog.push()
    try:
        new_variants = await llm_router.get_analysis_variants(
            text,
            extra_prompt=comment,
            model=prefs.llm_model,
        )
    except (ServiceDisabledError, LLMError) as e:
        err_kb = get_error_keyboard(
            retry_callback=f"retry:llm_variants:{video_id}",
            back_callback=f"nav:analysis:{video_id}",
        )
        await prog.show_error("AI: варианты", e.message, reply_markup=err_kb)
        return
    except Exception as e:
        logger.error(f"LLM variants refresh error: {e}")
        err_kb = get_error_keyboard(
            retry_callback=f"retry:llm_variants:{video_id}",
            back_callback=f"nav:analysis:{video_id}",
        )
        await prog.show_error(
            "AI: варианты",
            "Ошибка генерации.",
            str(e),
            reply_markup=err_kb,
        )
        return
    finally:
        await prog.stop()

    existing = task_data.get("variants", [])
    max_idx = max((v["idx"] for v in existing), default=0)
    for i, v in enumerate(new_variants, 1):
        v["idx"] = max_idx + i
    all_variants = existing + new_variants
    task_data["variants"] = all_variants
    db.save_variants(task.video_id, all_variants)

    await _show_analysis_menu(anchor, video_id, task_data)


@router.callback_query(F.data.startswith("analyze_run:"))
async def cb_llm_analyze(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    video_id, slot_idx = parts[1], int(parts[2])
    task_data = task_session.require_session(video_id, callback.from_user.id)
    if not task_data:
        await callback.answer("❌ Видео не найдено.", show_alert=True)
        return

    task: MediaTask = task_data["task"]
    text = _load_transcript_for_task(task_data) or ""
    if not text.strip():
        await callback.answer("❌ Сначала нужен транскрипт.", show_alert=True)
        await callback.message.edit_text(
            "❌ Транскрипт не найден. Нажмите «📝 Транскрибировать» или отправьте ссылку заново.",
            parse_mode=None,
        )
        return

    await callback.answer()
    await _run_llm_analyze(callback.message, video_id, slot_idx, callback.from_user.id)


async def _show_llm_result_screen(
    target,
    video_id: str,
    result_id: int,
    user_id: int,
) -> None:
    """Возвращает экран результата AI-анализа (после чата или отмены)."""
    task_data = task_session.require_session(video_id, user_id)
    if not task_data:
        return
    task: MediaTask = task_data["task"]
    r = db.get_analysis_result(result_id)
    if not r:
        return
    text = _load_transcript_for_task(task_data) or ""
    chat_settings = user_settings.get_chat_output_settings(user_id)
    state = db.get_state(task.video_id)
    gdrive_url = state.gdrive_transcript_url if state else None
    await target.edit_text(
        screens.llm_result_text(
            task,
            r.label,
            r.result,
            transcript_len=len(text),
            gdrive_url=gdrive_url,
            show_body=chat_settings.show_llm_in_chat,
        ),
        reply_markup=get_ai_result_keyboard(video_id, result_id),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("ai_chat_start:"))
async def cb_ai_chat_start(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Неверный запрос.", show_alert=True)
        return
    video_id, result_id = parts[1], int(parts[2])
    task_data = task_session.require_session(video_id, callback.from_user.id)
    if not task_data:
        await callback.answer("Видео не найдено.", show_alert=True)
        return
    r = db.get_analysis_result(result_id)
    if not r:
        await callback.answer("Ответ не найден.", show_alert=True)
        return

    task: MediaTask = task_data["task"]
    turns = get_chat_turn_count(task.video_id, result_id)
    await state.update_data(
        chat_video_id=video_id,
        chat_result_id=result_id,
        chat_msg_id=callback.message.message_id,
        chat_chat_id=callback.message.chat.id,
    )
    await state.set_state(AnalysisChatState.waiting_message)
    await callback.answer()
    await callback.message.edit_text(
        screens.ai_chat_prompt_text(r.label, task, turns=turns),
        reply_markup=get_ai_chat_keyboard(video_id, result_id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("ai_chat_done:"))
async def cb_ai_chat_done(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Неверный запрос.", show_alert=True)
        return
    video_id, result_id = parts[1], int(parts[2])
    await state.clear()
    await callback.answer("Диалог завершён")
    await _show_llm_result_screen(callback.message, video_id, result_id, callback.from_user.id)


@router.callback_query(F.data.startswith("ai_chat_full:"))
async def cb_ai_chat_full(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Неверный запрос.", show_alert=True)
        return
    video_id, _result_id = parts[1], int(parts[2])
    await state.update_data(chat_policy_override="full_transcript")
    await callback.answer(
        "Следующий ответ будет с полным транскриптом в контексте.",
        show_alert=True,
    )


@router.message(AnalysisChatState.waiting_message, F.text == "/cancel")
async def cb_ai_chat_cancel_cmd(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    video_id = data.get("chat_video_id")
    result_id = data.get("chat_result_id")
    chat_id = data.get("chat_chat_id")
    msg_id = data.get("chat_msg_id")
    await state.clear()
    await delete_message_safe(message.bot, message.chat.id, message.message_id)
    if not video_id or not result_id or not chat_id or not msg_id:
        await message.answer("Отменено.")
        return
    anchor = EditTarget(message.bot, chat_id, msg_id)
    await _show_llm_result_screen(anchor, video_id, result_id, message.from_user.id)


@router.message(AnalysisChatState.waiting_message)
async def cb_ai_chat_message(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    video_id = data.get("chat_video_id")
    result_id = data.get("chat_result_id")
    chat_id = data.get("chat_chat_id")
    msg_id = data.get("chat_msg_id")
    policy_override = data.get("chat_policy_override")
    if policy_override:
        await state.update_data(chat_policy_override=None)

    task_data = task_session.require_session(video_id, message.from_user.id) if video_id else None
    if not task_data or not chat_id or not msg_id:
        await state.clear()
        await message.answer("Видео не найдено. Отправьте ссылку заново.")
        return

    user_text = (message.text or "").strip()
    if not user_text:
        await message.answer("Напишите вопрос текстом.")
        return

    await delete_message_safe(message.bot, message.chat.id, message.message_id)

    task: MediaTask = task_data["task"]
    r = db.get_analysis_result(result_id)
    if not r:
        await state.clear()
        await message.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text="Ответ AI не найден.",
            parse_mode=None,
        )
        return

    anchor = EditTarget(message.bot, chat_id, msg_id)
    prefs = user_settings.get_user_settings(message.from_user.id)
    prog = ProgressReporter(anchor, title=f"💬 {r.label}")
    await prog.start()
    prog.set_stage(
        "AI-чат",
        f"POST {OMNIROUTE_BASE_URL}/chat/completions",
        subdetail=f"Модель: {prefs.llm_model}",
        stage_key=timing_stats.STAGE_LLM_ANALYZE,
    )
    await prog.push()
    try:
        answer = await run_analysis_chat(
            task.video_id,
            result_id,
            user_text,
            user_id=message.from_user.id,
            model=prefs.llm_model,
            policy=policy_override,
        )
    except (ServiceDisabledError, LLMError) as e:
        err_kb = get_ai_chat_keyboard(video_id, result_id)
        await prog.show_error("AI-чат", e.message, reply_markup=err_kb)
        return
    except Exception as e:
        logger.error("Analysis chat error: %s", e)
        err_kb = get_ai_chat_keyboard(video_id, result_id)
        await prog.show_error("AI-чат", "Ошибка при ответе.", str(e), reply_markup=err_kb)
        return
    finally:
        await prog.stop()

    turns = get_chat_turn_count(task.video_id, result_id)
    db_state = db.get_state(task.video_id)
    gdrive_url = db_state.gdrive_transcript_url if db_state else None
    await anchor.edit_text(
        screens.ai_chat_reply_text(
            r.label,
            task,
            question=user_text,
            answer=answer,
            turns=turns,
            gdrive_url=gdrive_url,
        ),
        reply_markup=get_ai_chat_keyboard(video_id, result_id),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


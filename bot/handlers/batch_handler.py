"""
bot/handlers/batch_handler.py — Пакетная транскрибация: .txt, несколько URL в тексте, очередь из БД.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from io import BytesIO

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery

from core.config import features
from core.exceptions import DownloadError, TranscriptionError, ServiceDisabledError
from bot.keyboards.batch import get_batch_confirm_keyboard
from bot.pipeline import download_audio_with_progress, save_transcript, transcribe_with_progress
from bot.ui.progress import ProgressReporter
from services import db
from services import timing_stats
from services import downloader
from utils.url_parser import extract_video_id, parse_urls_from_text
from utils.batch_import import is_text_document, is_zip_document, load_import_text
from utils.telegram_format import bold_html, escape_html

router = Router(name="batch_handler")
logger = logging.getLogger(__name__)

MAX_URLS_PER_BATCH = 200
MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB (лимит Telegram для бота)

_REPLY_BUTTONS = frozenset({
    "📥 Новая ссылка",
    "📜 История",
    "📦 Пакет",
    "⚙️ Настройки",
    "☁️ GDrive sync",
    "🧹 Очистить чат",
    "ℹ️ Помощь",
})

_pending_batches: dict[str, dict] = {}
_active_batch_users: set[int] = set()


@dataclass
class BatchItemResult:
    url: str
    title: str = ""
    ok: bool = False
    skipped: bool = False
    error: str = ""


@dataclass
class BatchReport:
    total_found: int = 0
    skipped_existing: int = 0
    processed: int = 0
    ok: int = 0
    failed: int = 0
    source: str = "файл"
    items: list[BatchItemResult] = field(default_factory=list)


def _split_pending_urls(urls: list[str]) -> tuple[list[str], int]:
    """Возвращает (urls без транскрипта, число пропущенных)."""
    pending: list[str] = []
    skipped = 0
    seen: set[str] = set()
    for url in urls:
        video_id = extract_video_id(url)
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        if db.has_transcript(video_id):
            skipped += 1
        else:
            pending.append(url)
    return pending, skipped


def _batch_busy(user_id: int | None) -> bool:
    return bool(user_id and user_id in _active_batch_users)


def _is_multi_url_message(text: str) -> bool:
    if not text or text in _REPLY_BUTTONS or text.startswith("/"):
        return False
    return len(parse_urls_from_text(text)) >= 2


async def _safe_edit(message: Message, text: str, **kwargs) -> None:
    """edit_text с retry и fallback (совместимость batch_handler)."""
    from bot.ui.telegram_safe import safe_edit_text

    ok = await safe_edit_text(message, text, **kwargs)
    if not ok:
        logger.warning("Batch _safe_edit failed")


def _batch_progress_text(i: int, total: int, url: str, *, title: str = "") -> str:
    lines = [
        f"📦 {bold_html('Пакетная обработка')}",
        f"Видео {bold_html(f'{i}/{total}')}",
    ]
    if title:
        lines.append(escape_html(title[:100]))
    lines.append(escape_html(url))
    return "\n".join(lines)


def _offer_batch_body(source: str, total_urls: int, skipped: int, pending: int) -> str:
    return (
        f"📦 {bold_html(f'Пакет: {source}')}\n\n"
        f"Найдено ссылок: {total_urls}\n"
        f"Уже в базе: {skipped}\n"
        f"К обработке: {bold_html(str(pending))}\n\n"
        f"<i>Транскрибация может занять много времени.</i>"
    )


async def _offer_batch(
    message: Message,
    urls: list[str],
    *,
    source: str,
    edit: Message | None = None,
) -> None:
    """Показывает подтверждение пакетной обработки."""
    if not features.transcript:
        text = "Транскрибация отключена (ENABLE_TRANSCRIPT=False)."
        if edit:
            await edit.edit_text(text, parse_mode=None)
        else:
            await message.answer(text, parse_mode=None)
        return

    user_id = message.from_user.id if message.from_user else 0
    if _batch_busy(user_id):
        text = "Уже идёт пакетная обработка. Дождитесь завершения."
        if edit:
            await edit.edit_text(text, parse_mode=None)
        else:
            await message.answer(text, parse_mode=None)
        return

    if len(urls) > MAX_URLS_PER_BATCH:
        text = f"Слишком много ссылок ({len(urls)}). Максимум: {MAX_URLS_PER_BATCH}."
        if edit:
            await edit.edit_text(text, parse_mode=None)
        else:
            await message.answer(text, parse_mode=None)
        return

    pending, skipped = _split_pending_urls(urls)

    if not pending:
        text = f"Найдено {len(urls)} ссылок — все уже есть в базе."
        if edit:
            await _safe_edit(edit, text, parse_mode=None)
        else:
            await message.answer(text, parse_mode=None)
        return

    batch_id = uuid.uuid4().hex[:8]
    _pending_batches[batch_id] = {
        "urls": pending,
        "user_id": user_id,
        "total_found": len(urls),
        "skipped_existing": skipped,
        "source": source,
    }

    body = _offer_batch_body(source, len(urls), skipped, len(pending))
    markup = get_batch_confirm_keyboard(batch_id, len(pending))
    if edit:
        await _safe_edit(edit, body, reply_markup=markup, parse_mode="HTML")
    else:
        await message.answer(body, reply_markup=markup, parse_mode="HTML")


async def _process_one_url(
    message: Message,
    url: str,
    user_id: int,
    index: int,
    total: int,
) -> BatchItemResult:
    batch_title = f"📦 Пакет [{index}/{total}]"
    video_id = extract_video_id(url)
    cached_entry = db.has_cached_metadata(video_id) if video_id else None

    if cached_entry:
        task = db.video_entry_to_task(cached_entry, url)
        logger.info(f"Batch metadata from DB: {task.video_id}")
    else:
        prog = ProgressReporter(message, title=batch_title)
        await prog.start()
        prog.set_stage(
            "Метаданные",
            "yt-dlp --dump-json",
            stage_key=timing_stats.STAGE_METADATA,
        )
        await prog.push()
        try:
            task = await downloader.get_info(url)
        except DownloadError as e:
            await prog.show_error("Метаданные", e.message)
            return BatchItemResult(url=url, error=e.message)
        except Exception as e:
            logger.error(f"Batch get_info error for {url}: {e}")
            await prog.show_error("Метаданные", "Не удалось получить метаданные.", str(e))
            return BatchItemResult(url=url, error="Не удалось получить метаданные")
        finally:
            await prog.stop()

    db.upsert_video(task, user_id)
    video_id = task.video_id
    if db.has_transcript(video_id):
        return BatchItemResult(url=url, title=task.title, ok=True, skipped=True)

    prefix_dl = f"{batch_title}\n⬇️ {task.title}"
    try:
        file_path = await download_audio_with_progress(message, url, task, prefix_dl)
        text = await transcribe_with_progress(message, task, file_path, user_id)
    except (ServiceDisabledError, DownloadError, TranscriptionError) as e:
        return BatchItemResult(url=url, title=task.title, error=getattr(e, "message", str(e)))
    except Exception as e:
        logger.error(f"Batch pipeline error for {url}: {e}")
        return BatchItemResult(url=url, title=task.title, error="Ошибка обработки")

    task_data = {"task": task, "url": url, "transcript": text, "user_id": user_id}
    await save_transcript(task_data, text, message, user_id=user_id)
    return BatchItemResult(url=url, title=task.title, ok=True)


def _format_report(report: BatchReport) -> str:
    lines = [
        f"📋 {bold_html('Итог пакетной обработки')}",
        f"Источник: {escape_html(report.source)}",
        f"В списке: {report.total_found} ссылок",
        f"Уже в базе: {report.skipped_existing}",
        f"Обработано: {report.processed}",
        f"✅ Успешно: {report.ok}",
        f"❌ Ошибок: {report.failed}",
    ]
    errors = [i for i in report.items if i.error and not i.skipped]
    if errors:
        lines.append(f"\n{bold_html('Ошибки:')}")
        for item in errors[:10]:
            title = item.title or item.url
            lines.append(
                f"• {escape_html(title[:50])}: {escape_html(item.error[:80])}"
            )
        if len(errors) > 10:
            lines.append(f"… и ещё {len(errors) - 10}")
    return "\n".join(lines)


async def _run_batch(
    message: Message,
    urls: list[str],
    user_id: int,
    total_found: int,
    skipped_existing: int,
    source: str,
) -> None:
    report = BatchReport(
        total_found=total_found,
        skipped_existing=skipped_existing,
        source=source,
    )
    total = len(urls)

    for i, url in enumerate(urls, 1):
        video_id = extract_video_id(url)
        title = ""
        if video_id:
            entry = db.get_video(video_id)
            if entry:
                title = entry.title

        await _safe_edit(
            message,
            _batch_progress_text(i, total, url, title=title),
            parse_mode="HTML",
        )
        result = await _process_one_url(message, url, user_id, i, total)
        if result.title and not title:
            title = result.title
        report.items.append(result)
        report.processed += 1
        if result.skipped:
            report.skipped_existing += 1
        elif result.ok:
            report.ok += 1
        else:
            report.failed += 1

    await _safe_edit(message, _format_report(report), parse_mode="HTML")
    logger.info(
        f"Batch complete [{source}]: ok={report.ok} failed={report.failed} "
        f"skipped={report.skipped_existing} total={report.total_found}"
    )


async def open_batch_from_db_panel(message: Message, *, edit: Message) -> None:
    """Пакет из БД — в inline-панели."""
    if not message.from_user:
        return
    urls = db.list_untranscribed_urls(message.from_user.id, limit=MAX_URLS_PER_BATCH)
    if not urls:
        from bot.keyboards.main_menu import get_back_home_keyboard

        await _safe_edit(
            edit,
            "В вашей истории нет видео без транскрипта.\n"
            "Отправьте ссылки или .txt с URL.",
            reply_markup=get_back_home_keyboard(),
            parse_mode=None,
        )
        return
    await _offer_batch(message, urls, source="база", edit=edit)


@router.message(F.text == "📦 Пакет")
async def btn_batch_from_db_legacy(message: Message) -> None:
    """Legacy: reply-кнопка больше не используется — подсказка."""
    await message.answer("Нажмите /start — меню теперь inline в одном сообщении.", parse_mode=None)


@router.message(F.text.func(_is_multi_url_message))
async def handle_multi_url_text(message: Message) -> None:
    """Несколько YouTube-ссылок в одном сообщении (без файла)."""
    urls = parse_urls_from_text(message.text or "")
    await _offer_batch(message, urls, source="сообщение")


@router.message(F.document)
async def handle_document_import(message: Message) -> None:
    """
    Пакетный импорт: .txt, text/plain, экспорт чата Telegram (.zip с messages.html).
    """
    doc = message.document
    if not doc:
        return

    logger.info(
        "Document import: file=%r mime=%r size=%s user=%s",
        doc.file_name,
        doc.mime_type,
        doc.file_size,
        message.from_user.id if message.from_user else None,
    )

    if message.from_user and _batch_busy(message.from_user.id):
        await message.answer("Уже идёт пакетная обработка. Дождитесь завершения.", parse_mode=None)
        return

    supported = is_text_document(file_name=doc.file_name, mime_type=doc.mime_type) or is_zip_document(
        file_name=doc.file_name, mime_type=doc.mime_type
    )
    if not supported:
        await message.answer(
            "Не удалось обработать этот файл.\n\n"
            "<b>Поддерживается:</b>\n"
            "• <code>.txt</code> со ссылками YouTube\n"
            "• экспорт чата Telegram — <code>.zip</code> (messages.html внутри)\n"
            "• текстовый файл без расширения (<code>text/plain</code>)\n\n"
            f"Ваш файл: <code>{doc.file_name or 'без имени'}</code>\n"
            f"Тип: <code>{doc.mime_type or 'не указан'}</code>",
            parse_mode="HTML",
        )
        return

    if doc.file_size and doc.file_size > MAX_FILE_BYTES:
        size_mb = doc.file_size / (1024 * 1024)
        limit_mb = MAX_FILE_BYTES / (1024 * 1024)
        await message.answer(
            f"Файл слишком большой ({size_mb:.1f} MB). Лимит: {limit_mb:.0f} MB.\n"
            "Разбейте экспорт на части или оставьте только сообщения со ссылками.",
            parse_mode=None,
        )
        return

    status = await message.answer("📄 Получил файл, читаю…", parse_mode=None)

    try:
        buf = BytesIO()
        await message.bot.download(doc, destination=buf)
        raw = buf.getvalue()
    except Exception as e:
        logger.error("Failed to download document: %s", e)
        await status.edit_text("Не удалось скачать файл из Telegram.", parse_mode=None)
        return

    text = load_import_text(raw, file_name=doc.file_name, mime_type=doc.mime_type)
    if not text or not text.strip():
        await status.edit_text(
            "Файл пустой или в архиве нет messages.html / .txt.\n"
            "Для экспорта Telegram: «Экспорт истории чата» → отправьте .zip боту.",
            parse_mode=None,
        )
        return

    urls = parse_urls_from_text(text)
    if not urls:
        preview = text.strip().replace("\n", " ")[:120]
        await status.edit_text(
            "YouTube-ссылки в файле не найдены.\n\n"
            f"Прочитано символов: {len(text)}\n"
            f"Начало: {preview}…\n\n"
            "Нужны ссылки вида youtube.com/watch?v=… или youtu.be/…",
            parse_mode=None,
        )
        return

    source = "экспорт Telegram (.zip)" if is_zip_document(
        file_name=doc.file_name, mime_type=doc.mime_type
    ) else "файл"
    await _offer_batch(message, urls, source=source, edit=status)


@router.callback_query(F.data.startswith("batch_cancel:"))
async def cb_batch_cancel(callback: CallbackQuery) -> None:
    batch_id = callback.data.split(":")[1]
    _pending_batches.pop(batch_id, None)
    await callback.answer("Отменено")
    await callback.message.edit_text("Пакетная обработка отменена.", parse_mode=None)


@router.callback_query(F.data.startswith("batch_start:"))
async def cb_batch_start(callback: CallbackQuery) -> None:
    batch_id = callback.data.split(":")[1]
    batch = _pending_batches.pop(batch_id, None)
    if not batch:
        await callback.answer("Задача устарела. Отправьте снова.", show_alert=True)
        return

    user_id = callback.from_user.id
    if batch["user_id"] != user_id:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    if user_id in _active_batch_users:
        await callback.answer("Уже идёт другая пакетная обработка.", show_alert=True)
        return

    await callback.answer()
    _active_batch_users.add(user_id)
    try:
        await _run_batch(
            callback.message,
            batch["urls"],
            user_id,
            batch["total_found"],
            batch["skipped_existing"],
            batch.get("source", "файл"),
        )
    except Exception as e:
        logger.exception("Batch run failed: %s", e)
        await _safe_edit(
            callback.message,
            f"❌ {bold_html('Пакет прерван')}\n{escape_html(str(e)[:300])}",
            parse_mode="HTML",
        )
    finally:
        _active_batch_users.discard(user_id)

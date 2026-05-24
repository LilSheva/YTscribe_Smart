"""Запуск GDrive sync в inline-панели."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery

from core.config import ENABLE_GDRIVE
from services import db
from services import gdrive_sync
from services.auto_summary import run_auto_summary_if_enabled
from bot.keyboards.main_menu import get_gdrive_sync_keyboard
from bot.ui.progress import ProgressReporter

router = Router(name="gdrive_sync")
logger = logging.getLogger(__name__)


def _sync_keyboard(report: gdrive_sync.AuditReport, user_id: int):
    missing = gdrive_sync.count_missing_summaries(user_id)
    if not report.issue_count and missing == 0:
        return get_gdrive_sync_keyboard([], missing_summary_count=0)
    return get_gdrive_sync_keyboard(
        report.affected_video_ids,
        missing_summary_count=missing,
    )


async def _run_audit(*, check_drive: bool = True) -> gdrive_sync.AuditReport:
    return await asyncio.to_thread(gdrive_sync.audit, check_drive=check_drive)


async def run_gdrive_sync_panel(message: Message, user_id: int) -> None:
    """Аудит GDrive в якорном сообщении панели."""
    if not ENABLE_GDRIVE:
        await message.edit_text(
            "Google Drive отключён (ENABLE_GDRIVE=False).",
            reply_markup=_sync_keyboard(gdrive_sync.AuditReport(), user_id),
            parse_mode=None,
        )
        return

    prog = ProgressReporter(message, title="☁️ GDrive sync")
    await prog.start()
    prog.set_stage("Аудит", "БД и папка Drive")
    await prog.push()
    try:
        report = await _run_audit()
    except Exception as e:
        logger.exception("GDrive audit in panel failed")
        from bot.ui.handler_fail import report_ui_failure

        await report_ui_failure(message, "GDrive sync", technical=str(e)[:200])
        return
    finally:
        await prog.stop()

    text = gdrive_sync.format_audit_report(report, html=True)
    kb = _sync_keyboard(report, user_id)
    from bot.ui.telegram_safe import safe_edit_text

    await safe_edit_text(
        message,
        text,
        reply_markup=kb,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "gdrive_sync:refresh")
async def cb_gdrive_refresh(callback: CallbackQuery) -> None:
    if not ENABLE_GDRIVE:
        await callback.answer("GDrive отключён.", show_alert=True)
        return

    await callback.answer("Проверяю…")
    await run_gdrive_sync_panel(callback.message, callback.from_user.id)


@router.callback_query(F.data == "gdrive_sync:repair")
async def cb_gdrive_repair(callback: CallbackQuery) -> None:
    if not ENABLE_GDRIVE:
        await callback.answer("GDrive отключён.", show_alert=True)
        return

    await callback.answer("Исправляю…")
    try:
        report = await _run_audit()
    except Exception as e:
        logger.exception("GDrive repair audit failed")
        from bot.ui.handler_fail import report_callback_failure

        await report_callback_failure(callback, "GDrive sync", e)
        return

    video_ids = report.affected_video_ids
    if not video_ids:
        text = gdrive_sync.format_audit_report(report, html=True)
        kb = _sync_keyboard(report, callback.from_user.id)
        await callback.message.edit_text(
            f"{text}\n\nНечего исправлять.",
            reply_markup=kb,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    total = len(video_ids)
    prog = ProgressReporter(callback.message, title="☁️ GDrive sync")
    await prog.start()
    batch = gdrive_sync.RepairBatchReport(dry_run=False, attempted=total)
    try:
        for i, video_id in enumerate(video_ids, start=1):
            entry = db.get_video(video_id)
            title = (entry.title[:36] + "…") if entry and len(entry.title) > 36 else (entry.title if entry else video_id)
            prog.set_stage(f"Исправление [{i}/{total}]", title)
            prog.set_pct(i / total)
            await prog.push()
            result = await asyncio.to_thread(gdrive_sync.repair, video_id)
            batch.results.append(result)
            if result.ok:
                batch.repaired += 1
            else:
                batch.failed += 1
    except Exception as e:
        logger.error("GDrive repair error: %s", e)
        await prog.show_error("GDrive sync", f"Ошибка синхронизации: {e}")
        return
    finally:
        await prog.stop()

    summary = gdrive_sync.format_repair_report(batch, html=True)
    report = await _run_audit()
    audit_text = gdrive_sync.format_audit_report(report, html=True)
    kb = _sync_keyboard(report, callback.from_user.id)
    await callback.message.edit_text(
        f"{summary}\n\n{audit_text}",
        reply_markup=kb,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "gdrive_sync:summaries")
async def cb_gdrive_summaries(callback: CallbackQuery) -> None:
    if not ENABLE_GDRIVE:
        await callback.answer("GDrive отключён.", show_alert=True)
        return

    user_id = callback.from_user.id
    video_ids = db.list_videos_missing_summary(user_id=user_id, limit=20)
    if not video_ids:
        await callback.answer("Все транскрипты уже с саммари.", show_alert=True)
        return

    await callback.answer("Запускаю AI…")
    total = len(video_ids)
    prog = ProgressReporter(callback.message, title="🧠 AI-саммари")
    await prog.start()

    ok = 0
    failed = 0
    try:
        for i, video_id in enumerate(video_ids, start=1):
            entry = db.get_video(video_id)
            title = (entry.title[:36] + "…") if entry and len(entry.title) > 36 else (entry.title if entry else video_id)
            prog.set_stage(f"Саммари [{i}/{total}]", title)
            prog.set_pct(i / total)
            await prog.push()

            if not entry:
                failed += 1
                continue
            task = db.video_entry_to_task(entry)
            text = db.get_transcript_text(video_id)
            if not text:
                failed += 1
                continue
            try:
                done = await run_auto_summary_if_enabled(task, text, user_id, message=None)
                if done:
                    ok += 1
                else:
                    failed += 1
            except Exception as e:
                logger.warning("Summary repair failed for %s: %s", video_id, e)
                failed += 1
    finally:
        await prog.stop()

    report = await _run_audit()
    lines = [
        "<b>AI-саммари — готово</b>",
        "",
        f"Успешно: {ok}",
        f"Ошибок: {failed}",
        "",
        gdrive_sync.format_audit_report(report, html=True),
    ]
    kb = _sync_keyboard(report, user_id)
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=kb,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

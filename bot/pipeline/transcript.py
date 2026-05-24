"""Сохранение транскрипта: JSON, GDrive, авто-саммари."""

from __future__ import annotations

import logging
from pathlib import Path

from aiogram.types import Message

from core.config import ENABLE_GDRIVE
from core.models import MediaTask
from services import db
from services import gdrive
from services import timing_stats
from services.auto_summary import run_auto_summary_if_enabled
from bot.ui.progress import ProgressReporter

logger = logging.getLogger(__name__)


async def save_transcript(
    task_data: dict,
    text: str,
    message: Message | None = None,
    *,
    user_id: int | None = None,
) -> str | None:
    """Сохраняет транскрипт в БД/на диск, синхронизирует хранилище, опционально авто-саммари."""
    task: MediaTask = task_data["task"]
    uid = user_id or task_data.get("user_id") or (message.chat.id if message else 0)
    prog = ProgressReporter(message, title=f"💾 {task.title}") if message else None
    if prog:
        await prog.start()
        prog.set_stage("Сохранение", "Запись JSON")
        await prog.push()

    task_data["transcript"] = text
    db.save_transcript_file(task, text, added_by_user_id=uid)

    gdrive_md_url: str | None = None
    if ENABLE_GDRIVE:
        state = db.get_state(task.video_id)
        json_path = Path(state.transcript_path) if state and state.transcript_path else None
        if json_path and json_path.exists():
            stage_label = (
                "Google Drive (локальная папка)"
                if gdrive.use_local_transcript_sync()
                else "Google Drive"
            )
            if prog:
                prog.set_stage(
                    stage_label,
                    "Синхронизация JSON",
                    stage_key=timing_stats.STAGE_GDRIVE_TRANSCRIPT,
                )
                await prog.push()
            try:
                existing = state.gdrive_transcript_url if state else None
                if existing and not existing.startswith("http"):
                    existing = None
                gdrive_result = await gdrive.sync_transcript(json_path, existing)
                gdrive_md_url = gdrive_result.public_url
                db.set_gdrive_transcript(task.video_id, gdrive_md_url)
            except Exception as e:
                logger.warning(f"Сохранение транскрипта в хранилище: {e}")
                if prog:
                    prog.set_detail(stage_label, subdetail=str(e)[:120])
                    await prog.push()
    if prog:
        await prog.stop()

    await run_auto_summary_if_enabled(task, text, uid, message)
    return gdrive_md_url

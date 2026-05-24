"""Сохранение LLM-ответа в БД, JSON и синхронизация хранилища."""

from __future__ import annotations

import logging

from core.config import ENABLE_GDRIVE, LLM_MODEL
from services import db
from services import gdrive

logger = logging.getLogger(__name__)


async def sync_transcript_storage(video_id: str) -> str | None:
    """Обновляет метаданные синхронизации после изменения JSON."""
    if not ENABLE_GDRIVE:
        return None
    from services.transcript_json import update_sync_metadata
    from services.transcript_paths import ensure_in_sync_folder, find_transcript_file

    path = ensure_in_sync_folder(video_id) if gdrive.use_local_transcript_sync() else find_transcript_file(video_id)
    if not path:
        return None
    state = db.get_state(video_id)
    try:
        if gdrive.use_local_transcript_sync():
            resolved = str(path.resolve())
            db.set_gdrive_transcript(video_id, resolved)
            db.set_gdrive_synced_after_llm(video_id)
            update_sync_metadata(video_id, resolved)
            logger.info("Transcript in Drive sync folder: %s", resolved)
            return resolved
        existing_url = ""
        if state and state.gdrive_transcript_url.startswith("http"):
            existing_url = state.gdrive_transcript_url
        result = await gdrive.sync_transcript(path, existing_url or None)
        db.set_gdrive_transcript(video_id, result.public_url)
        db.set_gdrive_synced_after_llm(video_id)
        update_sync_metadata(video_id, result.public_url)
        return result.public_url
    except Exception as e:
        logger.warning("sync_transcript_storage failed for %s: %s", video_id, e)
        return None


async def persist_llm_result(
    video_id: str,
    label: str,
    prompt: str,
    result: str,
    *,
    model: str | None = None,
) -> int:
    """Пишет ответ в БД и JSON; синхронизирует хранилище."""
    from services.transcript_json import append_ai_analysis

    llm_model = model or LLM_MODEL
    result_id = db.add_analysis_result(video_id, label, prompt, result)
    if not append_ai_analysis(video_id, result_id, label, prompt, result, model=llm_model):
        logger.error("persist_llm_result: failed to write JSON for %s", video_id)
    db.record_llm_call(video_id, prompt)
    await sync_transcript_storage(video_id)
    return result_id

"""Чтение/запись JSON-транскриптов (source of truth)."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from core.config import LLM_MODEL
from core.models import MediaTask
from services import db
from services.transcript_paths import find_transcript_file
from services.transcript_storage import get_transcripts_dir
from utils.json_format import (
    build_ai_entry,
    build_chat_message,
    build_document,
    empty_chat,
    load_document,
    next_chat_message_id,
    save_document,
    transcript_filename,
    validate_document,
)

logger = logging.getLogger(__name__)


def json_path_for_task(task: MediaTask) -> Path:
    return get_transcripts_dir() / transcript_filename(task.video_id, task.title)


def load_video_document(video_id: str) -> tuple[Path, dict[str, Any]] | None:
    path = find_transcript_file(video_id)
    if not path or path.suffix.lower() != ".json":
        return None
    return path, load_document(path)


def create_transcript_json(
    task: MediaTask,
    transcript_text: str,
    *,
    added_by_user_id: int = 0,
) -> Path:
    entry = db.get_video(task.video_id)
    added_at = entry.added_at if entry else ""
    uid = added_by_user_id or (entry.added_by_user_id if entry else 0)

    doc = build_document(
        task,
        transcript_text,
        added_by_user_id=uid,
        added_at=added_at,
    )
    path = json_path_for_task(task)
    save_document(path, doc)
    db.set_transcript(task.video_id, str(path.resolve()))
    logger.info("Created transcript JSON: %s", path.name)
    return path


def save_document_for_video(video_id: str, doc: dict[str, Any]) -> Path | None:
    path = find_transcript_file(video_id)
    if not path:
        path = json_path_for_task(
            db.video_entry_to_task(db.get_video(video_id))  # type: ignore[arg-type]
        )
    issues = validate_document(doc)
    if issues:
        logger.warning("JSON validation issues for %s: %s", video_id, issues)
    save_document(path, doc)
    db.set_transcript(video_id, str(path.resolve()))
    return path


def append_ai_analysis(
    video_id: str,
    entry_id: int,
    label: str,
    prompt: str,
    body: str,
    *,
    model: str | None = None,
) -> bool:
    loaded = load_video_document(video_id)
    if not loaded:
        logger.error("append_ai_analysis: JSON not found for %s", video_id)
        return False
    path, doc = loaded
    doc.setdefault("ai_analysis", [])
    doc["ai_analysis"].append(
        build_ai_entry(
            entry_id,
            label,
            prompt,
            body,
            model=model or LLM_MODEL,
        )
    )
    save_document(path, doc)
    return True


def sync_ai_from_db(video_id: str) -> bool:
    """Пересобирает ai_analysis в JSON из analysis_results (чаты сохраняются)."""
    loaded = load_video_document(video_id)
    if not loaded:
        return False
    path, doc = loaded

    existing_chats: dict[int, dict] = {}
    for entry in doc.get("ai_analysis") or []:
        eid = entry.get("id")
        if isinstance(eid, int) and "chat" in entry:
            existing_chats[eid] = entry["chat"]

    new_entries = []
    for r in db.get_analysis_results(video_id):
        chat = existing_chats.get(r.id) or empty_chat()
        new_entries.append(
            build_ai_entry(
                r.id,
                r.label,
                r.prompt,
                r.result,
                model=LLM_MODEL,
                created_at=r.created_at,
                chat=chat,
            )
        )
    doc["ai_analysis"] = new_entries
    save_document(path, doc)
    return True


def append_chat_message(
    video_id: str,
    analysis_id: int,
    role: str,
    content: str,
    *,
    model: str | None = None,
) -> bool:
    loaded = load_video_document(video_id)
    if not loaded:
        return False
    path, doc = loaded
    entry = next((e for e in doc.get("ai_analysis", []) if e.get("id") == analysis_id), None)
    if not entry:
        return False
    chat = entry.setdefault("chat", empty_chat())
    msg_id = next_chat_message_id(entry)
    chat.setdefault("messages", []).append(
        build_chat_message(msg_id, role, content, model=model)
    )
    from datetime import datetime

    chat["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_document(path, doc)
    return True


def update_sync_metadata(video_id: str, gdrive_path: str) -> None:
    loaded = load_video_document(video_id)
    if not loaded:
        return
    path, doc = loaded
    from datetime import datetime

    doc["sync"] = {
        "gdrive_path": gdrive_path,
        "gdrive_synced_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_document(path, doc)


def move_legacy_md_to_old(video_id: str) -> None:
    """Переносит .md дубликаты в old/ после миграции."""
    for directory in (get_transcripts_dir(),):
        old_dir = directory / "old"
        old_dir.mkdir(parents=True, exist_ok=True)
        for md in directory.glob(f"{video_id}_*.md"):
            target = old_dir / md.name
            if target.exists():
                target.unlink()
            shutil.move(str(md), str(target))
            logger.info("Moved legacy MD to old/: %s", md.name)

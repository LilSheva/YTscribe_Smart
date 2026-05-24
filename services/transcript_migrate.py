"""On-demand миграция legacy .md транскриптов в JSON v1.0."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from core.config import LLM_MODEL
from services import db
from services.transcript_json import move_legacy_md_to_old
from services.transcript_storage import LOCAL_TRANSCRIPTS_SUBDIR, get_transcripts_dir
from utils.json_format import (
    build_ai_entry,
    build_document,
    empty_chat,
    load_document,
    save_document,
    transcript_filename,
    validate_document,
)
from utils.md_format import extract_processed_at, extract_transcript_body

logger = logging.getLogger(__name__)


def _old_dir(base: Path) -> Path:
    d = base / "old"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _find_md(video_id: str) -> Path | None:
    for directory in (get_transcripts_dir(), LOCAL_TRANSCRIPTS_SUBDIR):
        if not directory.is_dir():
            continue
        matches = sorted(directory.glob(f"{video_id}_*.md"))
        if matches:
            return matches[0]
    return None


def _json_target(task, base_dir: Path) -> Path:
    return base_dir / transcript_filename(task.video_id, task.title)


def migrate_one(video_id: str, *, execute: bool = True) -> tuple[bool, str]:
    """Мигрирует один video_id из .md в JSON. Возвращает (success, message)."""
    entry = db.get_video(video_id)
    if not entry:
        return False, "нет записи в videos"

    md_path = _find_md(video_id)
    if not md_path:
        existing_json = sorted(get_transcripts_dir().glob(f"{video_id}_*.json"))
        if existing_json:
            return True, "уже JSON"
        return False, "нет .md"

    task = db.video_entry_to_task(entry)
    raw_md = md_path.read_text(encoding="utf-8")
    transcript = extract_transcript_body(raw_md)
    if not transcript:
        return False, "пустой транскрипт в .md"

    processed_at = extract_processed_at(raw_md)
    ai_entries = [
        build_ai_entry(
            r.id,
            r.label,
            r.prompt,
            r.result,
            model=LLM_MODEL,
            created_at=r.created_at,
            chat=empty_chat(),
        )
        for r in db.get_analysis_results(video_id)
    ]

    doc = build_document(
        task,
        transcript,
        added_by_user_id=entry.added_by_user_id,
        added_at=entry.added_at,
        processed_at=processed_at,
        ai_analysis=ai_entries,
    )

    issues = validate_document(doc)
    if issues:
        return False, f"validation: {issues[0]}"

    target_dir = get_transcripts_dir()
    json_path = _json_target(task, target_dir)

    if not execute:
        return True, f"-> {json_path.name}"

    save_document(json_path, doc)
    db.set_transcript(video_id, str(json_path.resolve()))

    old = _old_dir(md_path.parent)
    dest = old / md_path.name
    if dest.exists():
        dest.unlink()
    shutil.move(str(md_path), str(dest))

    move_legacy_md_to_old(video_id)
    logger.info("Migrated %s: %s", video_id, json_path.name)
    return True, json_path.name


def ensure_json_transcript(video_id: str) -> bool:
    """Если есть только .md — мигрирует в JSON. True если JSON доступен."""
    existing_json = sorted(get_transcripts_dir().glob(f"{video_id}_*.json"))
    if existing_json:
        return True
    for directory in (get_transcripts_dir(), LOCAL_TRANSCRIPTS_SUBDIR):
        if directory.is_dir() and list(directory.glob(f"{video_id}_*.json")):
            return True

    md_path = _find_md(video_id)
    if not md_path:
        return False

    ok, msg = migrate_one(video_id, execute=True)
    if not ok:
        logger.warning("MD→JSON migrate failed for %s: %s", video_id, msg)
    return ok

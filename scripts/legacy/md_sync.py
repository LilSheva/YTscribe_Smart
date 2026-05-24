"""Legacy: нормализация .md транскриптов (до перехода на JSON v1.0)."""

from __future__ import annotations

import logging

from services import db
from services.transcript_paths import find_transcript_file
from utils.md_format import (
    extract_processed_at,
    extract_transcript_body,
    render_document,
    rebuild_content_with_ai,
    validate_document,
)

logger = logging.getLogger(__name__)


def _ai_entries_for_video(video_id: str) -> list[dict]:
    return [
        {
            "label": r.label,
            "prompt": r.prompt,
            "result": r.result,
            "created_at": (r.created_at or "")[:16],
        }
        for r in db.get_analysis_results(video_id)
    ]


def rebuild_ai_sections_from_db(video_id: str) -> bool:
    path = find_transcript_file(video_id)
    if not path:
        logger.warning("rebuild_ai: file not found for %s", video_id)
        return False

    entries = _ai_entries_for_video(video_id)
    base = path.read_text(encoding="utf-8")
    new_content = rebuild_content_with_ai(base, entries)
    path.write_text(new_content, encoding="utf-8")
    logger.info("rebuild_ai: %s (%s entries) -> %s", video_id, len(entries), path.name)
    return True


def normalize_transcript_md(video_id: str, *, include_ai: bool = True) -> bool:
    path = find_transcript_file(video_id)
    if not path:
        logger.warning("normalize_md: file not found for %s", video_id)
        return False

    entry = db.get_video(video_id)
    if not entry:
        logger.warning("normalize_md: no video row for %s", video_id)
        return False

    raw = path.read_text(encoding="utf-8")
    transcript = extract_transcript_body(raw)
    if not transcript:
        logger.warning("normalize_md: empty transcript for %s", video_id)
        return False

    task = db.video_entry_to_task(entry)
    processed_at = extract_processed_at(raw)
    ai_entries = _ai_entries_for_video(video_id) if include_ai else []

    content = render_document(
        task,
        transcript,
        ai_entries,
        processed_at=processed_at,
    )
    path.write_text(content, encoding="utf-8")
    logger.info("normalize_md: %s -> %s", video_id, path.name)
    return True


def normalize_all_transcripts(
    limit: int = 10_000,
    *,
    include_ai: bool = True,
) -> tuple[int, int, list[tuple[str, list[str]]]]:
    records = db.list_transcribed(limit=limit)
    ok = failed = 0
    remaining_issues: list[tuple[str, list[str]]] = []

    for rec in records:
        video_id = rec["video_id"]
        if normalize_transcript_md(video_id, include_ai=include_ai):
            ok += 1
        else:
            failed += 1
            continue

        path = find_transcript_file(video_id)
        if path:
            issues = validate_document(path.read_text(encoding="utf-8"))
            if issues:
                remaining_issues.append((video_id, issues))

    return ok, failed, remaining_issues


def strip_ai_sections(video_id: str) -> bool:
    return normalize_transcript_md(video_id, include_ai=False)


def strip_ai_sections_all(limit: int = 10_000) -> tuple[int, int]:
    ok, failed, _ = normalize_all_transcripts(limit=limit, include_ai=False)
    return ok, failed


def audit_md_structure(limit: int = 10_000) -> list[tuple[str, str, list[str]]]:
    bad: list[tuple[str, str, list[str]]] = []
    for rec in db.list_transcribed(limit=limit):
        video_id = rec["video_id"]
        path = find_transcript_file(video_id)
        if not path:
            bad.append((video_id, rec.get("title") or video_id, ["файл не найден"]))
            continue
        issues = validate_document(path.read_text(encoding="utf-8"))
        if issues:
            bad.append((video_id, rec.get("title") or video_id, issues))
    return bad

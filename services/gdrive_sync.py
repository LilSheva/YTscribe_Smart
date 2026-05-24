"""
services/gdrive_sync.py — Аудит и восстановление синхронизации транскриптов с Google Drive.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.config import ENABLE_GDRIVE, GDRIVE_TRANSCRIPTS_FOLDER_ID
from services import db
from services import gdrive
from services.transcript_json import sync_ai_from_db
from services.transcript_paths import ensure_in_sync_folder, find_transcript_file
from services.transcript_storage import is_in_sync_dir
from utils.json_format import ai_entry_count, load_document, validate_document

logger = logging.getLogger(__name__)

KIND_LABELS = {
    "missing_on_drive": "нет на Drive",
    "stale_after_llm": "AI не на Drive",
    "json_llm_mismatch": "расхождение AI",
    "missing_summary": "нет саммари",
    "invalid_json": "невалидный JSON",
    "outside_sync_folder": "файл не в папке Drive",
    "local_newer": "локальный JSON новее",
    "no_local_file": "нет локального JSON",
}


@dataclass
class SyncIssue:
    video_id: str
    title: str
    kind: str
    detail: str


@dataclass
class AuditReport:
    scanned: int = 0
    ok: int = 0
    issues: list[SyncIssue] = field(default_factory=list)

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    @property
    def affected_video_ids(self) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for issue in self.issues:
            if issue.video_id in seen:
                continue
            seen.add(issue.video_id)
            ordered.append(issue.video_id)
        return ordered


@dataclass
class RepairResult:
    video_id: str
    title: str
    ok: bool
    action: str = ""
    error: str = ""


@dataclass
class RepairBatchReport:
    dry_run: bool
    attempted: int = 0
    repaired: int = 0
    failed: int = 0
    results: list[RepairResult] = field(default_factory=list)


def count_ai_in_file(path: Path) -> int:
    if not path.exists():
        return 0
    if path.suffix.lower() == ".json":
        try:
            return ai_entry_count(load_document(path))
        except Exception:
            return 0
    from utils.md_format import ai_section_count
    return ai_section_count(path.read_text(encoding="utf-8"))


def count_json_validation_issues(path: Path) -> list[str]:
    if path.suffix.lower() != ".json":
        return []
    try:
        return validate_document(load_document(path))
    except Exception as e:
        return [str(e)]


def count_missing_summaries(user_id: int | None = None) -> int:
    return len(db.list_videos_missing_summary(user_id=user_id))


def _parse_sync_time(value: str) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _audit_record(rec: dict, *, check_drive: bool) -> list[SyncIssue]:
    issues: list[SyncIssue] = []
    video_id = rec["video_id"]
    title = rec.get("title") or video_id
    path = find_transcript_file(video_id)

    if not path:
        path_str = rec.get("transcript_path") or ""
        issues.append(SyncIssue(video_id, title, "no_local_file", path_str or "путь не задан"))
        return issues

    if gdrive.use_local_transcript_sync() and not is_in_sync_dir(path):
        issues.append(
            SyncIssue(
                video_id,
                title,
                "outside_sync_folder",
                f"файл в {path.parent}, нужна папка GDRIVE_LOCAL_DIR",
            )
        )

    if not rec.get("has_summary"):
        issues.append(SyncIssue(video_id, title, "missing_summary", "транскрипт без AI-саммари"))

    if gdrive.use_local_transcript_sync():
        if not rec.get("gdrive_transcript_url"):
            issues.append(
                SyncIssue(video_id, title, "missing_on_drive", "путь в БД не задан (local mode)")
            )
    else:
        url = rec.get("gdrive_transcript_url") or ""
        file_id = gdrive.extract_file_id(url)
        if not url or not file_id:
            issues.append(SyncIssue(video_id, title, "missing_on_drive", "ссылка GDrive отсутствует"))
        elif check_drive and not gdrive.check_file_exists_sync(file_id):
            issues.append(SyncIssue(video_id, title, "missing_on_drive", "файл на Drive не найден (404)"))

    if rec.get("has_summary") and not rec.get("gdrive_updated_after_llm"):
        issues.append(SyncIssue(video_id, title, "stale_after_llm", "есть AI-анализ, файл не отмечен синхронизированным"))

    db_ai = len(db.get_analysis_results(video_id))
    file_ai = count_ai_in_file(path)
    if db_ai != file_ai:
        issues.append(
            SyncIssue(
                video_id,
                title,
                "json_llm_mismatch",
                f"БД: {db_ai} ответов, JSON: {file_ai} записей",
            )
        )

    json_issues = count_json_validation_issues(path)
    if json_issues:
        issues.append(
            SyncIssue(
                video_id,
                title,
                "invalid_json",
                json_issues[0][:80],
            )
        )

    sync_ts = _parse_sync_time(rec.get("gdrive_transcript_synced_at") or "")
    if sync_ts is not None and path.stat().st_mtime > sync_ts + 2:
        if not any(i.kind == "stale_after_llm" for i in issues):
            issues.append(
                SyncIssue(video_id, title, "local_newer", "локальный файл новее последней отметки sync")
            )

    return issues


def audit(*, check_drive: bool = True, limit: int = 500) -> AuditReport:
    """Проверяет расхождения между БД, локальными .md и Google Drive."""
    if not ENABLE_GDRIVE:
        return AuditReport()
    if gdrive.use_local_transcript_sync():
        check_drive = False

    records = db.list_sync_records(limit=limit)
    issues: list[SyncIssue] = []
    ok = 0

    for rec in records:
        rec_issues = _audit_record(rec, check_drive=check_drive)
        if rec_issues:
            issues.extend(rec_issues)
        else:
            ok += 1

    logger.info(
        "GDrive audit: scanned=%s ok=%s issues=%s",
        len(records),
        ok,
        len(issues),
    )
    return AuditReport(scanned=len(records), ok=ok, issues=issues)


def repair(video_id: str) -> RepairResult:
    """Синхронизирует AI в JSON из БД и обновляет GDrive."""
    if not ENABLE_GDRIVE:
        return RepairResult(video_id, video_id, ok=False, error="GDrive отключён")

    entry = db.get_video(video_id)
    state = db.get_state(video_id)
    title = entry.title if entry else video_id

    path = find_transcript_file(video_id)
    if not path:
        return RepairResult(video_id, title, ok=False, error="нет локального транскрипта")

    sync_ai_from_db(video_id)

    was_outside = gdrive.use_local_transcript_sync() and not is_in_sync_dir(path)
    if gdrive.use_local_transcript_sync():
        path = ensure_in_sync_folder(video_id)
        if not path:
            return RepairResult(video_id, title, ok=False, error="не удалось перенести в GDRIVE_LOCAL_DIR")
    else:
        path = find_transcript_file(video_id) or path

    try:
        gdrive._check_enabled()
        if gdrive.use_local_transcript_sync():
            resolved = str(path.resolve())
            db.set_gdrive_transcript(video_id, resolved)
            has_ai = bool(state and state.has_summary) or count_ai_in_file(path) > 0
            if has_ai:
                db.set_gdrive_synced_after_llm(video_id)
            logger.info("GDrive repair OK (local) [%s]: %s", video_id, resolved)
            action = "migrated" if was_outside else "local_sync"
            return RepairResult(video_id, title, ok=True, action=action)

        existing_url = (state.gdrive_transcript_url if state else "") or None
        file_id = gdrive.extract_file_id(existing_url or "")
        if file_id and gdrive.check_file_exists_sync(file_id):
            result = gdrive._update_file_sync(path, file_id)
            action = "updated"
        else:
            result = gdrive._upload_file_sync(path, GDRIVE_TRANSCRIPTS_FOLDER_ID)
            action = "uploaded"

        db.set_gdrive_transcript(video_id, result.public_url)

        has_ai = bool(state and state.has_summary) or count_ai_in_file(path) > 0
        if has_ai:
            db.set_gdrive_synced_after_llm(video_id)

        logger.info("GDrive repair OK [%s]: %s -> %s", video_id, action, result.public_url)
        return RepairResult(video_id, title, ok=True, action=action)
    except Exception as e:
        logger.warning("GDrive repair failed [%s]: %s", video_id, e)
        return RepairResult(video_id, title, ok=False, error=str(e))


def repair_all(*, dry_run: bool = True, check_drive: bool = True, limit: int = 500) -> RepairBatchReport:
    """Исправляет все видео с проблемами из audit()."""
    report = audit(check_drive=check_drive, limit=limit)
    video_ids = report.affected_video_ids

    if dry_run:
        return RepairBatchReport(dry_run=True, attempted=len(video_ids))

    batch = RepairBatchReport(dry_run=False, attempted=len(video_ids))
    for video_id in video_ids:
        result = repair(video_id)
        batch.results.append(result)
        if result.ok:
            batch.repaired += 1
        else:
            batch.failed += 1

    logger.info(
        "GDrive repair_all: attempted=%s repaired=%s failed=%s",
        batch.attempted,
        batch.repaired,
        batch.failed,
    )
    return batch


def format_audit_report(report: AuditReport, *, html: bool = True) -> str:
    """Текст отчёта для консоли или Telegram."""
    if not ENABLE_GDRIVE:
        return "GDrive отключён (ENABLE_GDRIVE=False)."

    lines = [
        "<b>GDrive sync — аудит</b>" if html else "GDrive sync — аудит",
        "",
        f"Проверено: {report.scanned}",
        f"OK: {report.ok}",
        f"Проблем: {report.issue_count} ({len(report.affected_video_ids)} видео)",
    ]

    if not report.issues:
        lines.append("")
        lines.append("Все транскрипты синхронизированы.")
        return "\n".join(lines)

    lines.append("")
    by_kind: dict[str, int] = {}
    for issue in report.issues:
        by_kind[issue.kind] = by_kind.get(issue.kind, 0) + 1

    for kind, count in sorted(by_kind.items(), key=lambda x: -x[1]):
        label = KIND_LABELS.get(kind, kind)
        lines.append(f"• {label}: {count}")

    lines.append("")
    lines.append("Примеры:")
    shown: set[str] = set()
    for issue in report.issues:
        if issue.video_id in shown:
            continue
        shown.add(issue.video_id)
        title = issue.title[:40] + ("…" if len(issue.title) > 40 else "")
        kind_label = KIND_LABELS.get(issue.kind, issue.kind)
        if html:
            lines.append(f"— {title} ({kind_label})")
        else:
            lines.append(f"- {title} ({kind_label})")
        if len(shown) >= 5:
            break

    return "\n".join(lines)


def format_repair_report(batch: RepairBatchReport, *, html: bool = True) -> str:
    if batch.dry_run:
        return (
            f"<b>GDrive sync</b>\n\nБудет исправлено видео: {batch.attempted}"
            if html
            else f"Будет исправлено видео: {batch.attempted}"
        )

    lines = [
        "<b>GDrive sync — готово</b>" if html else "GDrive sync — готово",
        "",
        f"Попыток: {batch.attempted}",
        f"Успешно: {batch.repaired}",
        f"Ошибок: {batch.failed}",
    ]
    for result in batch.results:
        if not result.ok:
            title = result.title[:35]
            lines.append(f"❌ {title}: {result.error[:80]}")
    return "\n".join(lines)

"""Разрешение пути к файлу транскрипта (.json, legacy .md)."""

from __future__ import annotations

import logging
from pathlib import Path

from services import db
from services.transcript_storage import LOCAL_TRANSCRIPTS_SUBDIR, get_transcripts_dir

logger = logging.getLogger(__name__)


def _glob_video_file(directory: Path, video_id: str) -> Path | None:
    for pattern in (f"{video_id}_*.json", f"{video_id}_*.md"):
        matches = sorted(directory.glob(pattern))
        if matches:
            return matches[0]
    return None


def find_transcript_file(video_id: str) -> Path | None:
    state = db.get_state(video_id)

    search_dirs: list[Path] = []
    try:
        search_dirs.append(get_transcripts_dir())
    except Exception:
        pass
    if state and state.transcript_path:
        search_dirs.append(Path(state.transcript_path).parent)
    search_dirs.append(LOCAL_TRANSCRIPTS_SUBDIR)

    seen: set[Path] = set()
    for directory in search_dirs:
        try:
            directory = directory.resolve()
        except OSError:
            continue
        if directory in seen or not directory.is_dir():
            continue
        seen.add(directory)
        found = _glob_video_file(directory, video_id)
        if found:
            if state and state.transcript_path != str(found):
                db.set_transcript(video_id, str(found))
                logger.info("Transcript path updated for %s -> %s", video_id, found)
            return found
    return None


def ensure_in_sync_folder(video_id: str) -> Path | None:
    """Находит файл и при необходимости копирует в папку синхронизации Drive."""
    from services.transcript_storage import migrate_file_to_sync_dir, use_local_drive_folder

    path = find_transcript_file(video_id)
    if not path:
        return None
    if use_local_drive_folder():
        return migrate_file_to_sync_dir(path, video_id)
    return path

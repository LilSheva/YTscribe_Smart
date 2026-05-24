"""Пути хранения транскриптов (.md): локальная data/ или папка Google Drive Desktop."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from core.config import DATA_DIR, GDRIVE_LOCAL_DIR, GDRIVE_MODE

logger = logging.getLogger(__name__)

LOCAL_TRANSCRIPTS_SUBDIR = DATA_DIR / "transcripts"


def use_local_drive_folder() -> bool:
    return GDRIVE_MODE == "local"


def get_transcripts_dir() -> Path:
    """
    Каталог для .md транскриптов.
    local: GDRIVE_LOCAL_DIR (синхронизируется приложением Google Drive).
    api: data/transcripts + загрузка через Drive API.
    """
    if use_local_drive_folder() and GDRIVE_LOCAL_DIR:
        target = GDRIVE_LOCAL_DIR.expanduser()
    else:
        target = LOCAL_TRANSCRIPTS_SUBDIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def is_in_sync_dir(path: Path) -> bool:
    """True если файл уже в целевой папке хранения (Drive или data/transcripts)."""
    try:
        return path.resolve().is_relative_to(get_transcripts_dir().resolve())
    except ValueError:
        return False


def migrate_file_to_sync_dir(source: Path, video_id: str) -> Path:
    """
    Копирует .md в get_transcripts_dir() и обновляет путь в БД.
    В local-режиме это папка Google Drive Desktop (Z: / G:).
    """
    from services import db

    target_dir = get_transcripts_dir()
    if is_in_sync_dir(source):
        db.set_transcript(video_id, str(source.resolve()))
        return source.resolve()

    target = target_dir / source.name
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
        logger.info("Transcript copied to sync folder: %s -> %s", source, target)

    resolved = str(target.resolve())
    db.set_transcript(video_id, resolved)
    return target.resolve()

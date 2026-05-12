"""
services/gdrive.py — Сервис загрузки файлов на Google Drive.

Обеспечивает:
  - Загрузку медиа-файлов в папку GDRIVE_MEDIA_FOLDER_ID.
  - Загрузку транскриптов (.md) в папку GDRIVE_TRANSCRIPTS_FOLDER_ID.
  - Получение публичной ссылки на файл.
  - Проверку Feature Toggle перед операцией.
"""

import asyncio
import logging
from pathlib import Path
from dataclasses import dataclass

from core.config import (
    ENABLE_GDRIVE,
    GDRIVE_CREDENTIALS_PATH,
    GDRIVE_MEDIA_FOLDER_ID,
    GDRIVE_TRANSCRIPTS_FOLDER_ID,
    BASE_DIR,
)
from core.exceptions import ServiceDisabledError, YTSBotError

logger = logging.getLogger(__name__)


class GDriveError(YTSBotError):
    """Ошибка при работе с Google Drive."""

    def __init__(self, detail: str = "Google Drive API недоступен") -> None:
        super().__init__(detail)


@dataclass
class GDriveResult:
    """Результат загрузки файла на GDrive."""

    file_name: str
    file_id: str
    public_url: str
    size_mb: float


def _check_enabled() -> None:
    """Проверяет, включён ли модуль GDrive."""
    if not ENABLE_GDRIVE:
        raise ServiceDisabledError("GDRIVE")


def _get_service():
    """Создаёт и возвращает Google Drive API service."""
    creds_path = BASE_DIR / GDRIVE_CREDENTIALS_PATH
    if not creds_path.exists():
        raise GDriveError(f"Credentials не найдены: {creds_path}")

    try:
        from googleapiclient.discovery import build
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_file(
            str(creds_path),
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )
        return build("drive", "v3", credentials=creds)
    except ImportError:
        raise GDriveError(
            "google-api-python-client не установлен. "
            "Выполните: pip install google-api-python-client google-auth"
        )
    except Exception as e:
        raise GDriveError(f"Ошибка авторизации GDrive: {e}") from e


def _upload_file_sync(file_path: Path, folder_id: str) -> GDriveResult:
    """
    Синхронная загрузка файла на GDrive в указанную папку.

    Args:
        file_path: Путь к локальному файлу.
        folder_id: ID целевой папки на GDrive.

    Returns:
        GDriveResult с информацией о загруженном файле.

    Raises:
        GDriveError: При ошибке загрузки.
    """
    if not file_path.exists():
        raise GDriveError(f"Файл не найден: {file_path}")

    try:
        from googleapiclient.http import MediaFileUpload

        service = _get_service()

        # Метаданные файла (с указанием папки)
        file_metadata: dict = {"name": file_path.name}
        if folder_id:
            file_metadata["parents"] = [folder_id]

        media = MediaFileUpload(str(file_path), resumable=True)

        uploaded = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name",
        ).execute()

        file_id = uploaded["id"]

        # Сделать файл публичным (доступ по ссылке)
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()

        public_url = f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"
        size_mb = file_path.stat().st_size / (1024 * 1024)

        logger.info(f"Загружен на GDrive: {file_path.name} ({size_mb:.1f} MB) → folder={folder_id[:8]}...")

        return GDriveResult(
            file_name=file_path.name,
            file_id=file_id,
            public_url=public_url,
            size_mb=size_mb,
        )

    except GDriveError:
        raise
    except Exception as e:
        raise GDriveError(f"Ошибка загрузки на GDrive: {e}") from e


# ===== PUBLIC ASYNC API =====


async def upload_media(file_path: Path) -> GDriveResult:
    """
    Асинхронно загружает медиа-файл (m4a/mp4) на Google Drive.

    Файл попадает в папку GDRIVE_MEDIA_FOLDER_ID.

    Args:
        file_path: Путь к локальному медиа-файлу.

    Returns:
        GDriveResult с публичной ссылкой.

    Raises:
        ServiceDisabledError: Если модуль отключён.
        GDriveError: При ошибке загрузки.
    """
    _check_enabled()
    logger.info(f"Загрузка медиа на GDrive: {file_path.name}")
    result = await asyncio.to_thread(_upload_file_sync, file_path, GDRIVE_MEDIA_FOLDER_ID)
    return result


async def upload_transcript(file_path: Path) -> GDriveResult:
    """
    Асинхронно загружает транскрипт (.md) на Google Drive.

    Файл попадает в папку GDRIVE_TRANSCRIPTS_FOLDER_ID.

    Args:
        file_path: Путь к .md файлу транскрипта.

    Returns:
        GDriveResult с публичной ссылкой.

    Raises:
        ServiceDisabledError: Если модуль отключён.
        GDriveError: При ошибке загрузки.
    """
    _check_enabled()
    logger.info(f"Загрузка транскрипта на GDrive: {file_path.name}")
    result = await asyncio.to_thread(_upload_file_sync, file_path, GDRIVE_TRANSCRIPTS_FOLDER_ID)
    return result


# Обратная совместимость (для url_handler)
async def upload(file_path: Path) -> GDriveResult:
    """Legacy-обёртка: определяет тип файла и загружает в нужную папку."""
    _check_enabled()
    if file_path.suffix == ".md":
        return await upload_transcript(file_path)
    else:
        return await upload_media(file_path)

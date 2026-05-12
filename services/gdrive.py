"""
services/gdrive.py — Сервис загрузки файлов на Google Drive.

Обеспечивает:
  - Загрузку файла на GDrive (публичная папка).
  - Получение публичной ссылки на файл.
  - Проверку Feature Toggle перед операцией.

TODO (Фаза 3): Полная реализация через google-api-python-client.
"""

import asyncio
import logging
from pathlib import Path
from dataclasses import dataclass

from core.config import ENABLE_GDRIVE, GDRIVE_CREDENTIALS_PATH, BASE_DIR
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


def _upload_file_sync(file_path: Path) -> GDriveResult:
    """
    Синхронная загрузка файла на GDrive (блокирующий вызов).

    Args:
        file_path: Путь к локальному файлу.

    Returns:
        GDriveResult с информацией о загруженном файле.

    Raises:
        GDriveError: При ошибке загрузки.
    """
    if not file_path.exists():
        raise GDriveError(f"Файл не найден: {file_path}")

    creds_path = BASE_DIR / GDRIVE_CREDENTIALS_PATH
    if not creds_path.exists():
        raise GDriveError(f"Credentials не найдены: {creds_path}")

    # TODO: Реализовать реальную загрузку через google-api-python-client
    # Пока — заглушка для интеграции с url_handler
    try:
        from googleapiclient.discovery import build
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_file(
            str(creds_path),
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )
        service = build("drive", "v3", credentials=creds)

        file_metadata = {"name": file_path.name}
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(str(file_path), resumable=True)

        uploaded = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, size",
        ).execute()

        file_id = uploaded["id"]

        # Сделать файл публичным
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()

        public_url = f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"
        size_mb = file_path.stat().st_size / (1024 * 1024)

        logger.info(f"Загружен на GDrive: {file_path.name} ({size_mb:.1f} MB)")

        return GDriveResult(
            file_name=file_path.name,
            file_id=file_id,
            public_url=public_url,
            size_mb=size_mb,
        )

    except ImportError:
        raise GDriveError(
            "google-api-python-client не установлен. "
            "Выполните: pip install google-api-python-client google-auth"
        )
    except Exception as e:
        raise GDriveError(f"Ошибка загрузки на GDrive: {e}") from e


# ===== PUBLIC ASYNC API =====


async def upload(file_path: Path) -> GDriveResult:
    """
    Асинхронно загружает файл на Google Drive.

    Args:
        file_path: Путь к локальному файлу.

    Returns:
        GDriveResult с публичной ссылкой.

    Raises:
        ServiceDisabledError: Если модуль отключён.
        GDriveError: При ошибке загрузки.
    """
    _check_enabled()
    logger.info(f"Загрузка на GDrive: {file_path.name}")

    result = await asyncio.to_thread(_upload_file_sync, file_path)
    return result

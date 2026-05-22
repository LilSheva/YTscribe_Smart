"""
services/gdrive.py — Сервис загрузки файлов на Google Drive.

Авторизация через OAuth 2.0 (Desktop App) — твой личный аккаунт.
При первом запуске откроется браузер для авторизации,
далее токен сохраняется в credentials/token.json.

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

# Путь к сохранённому токену (после первой авторизации)
TOKEN_PATH: Path = BASE_DIR / "credentials" / "token.json"

# Скоупы доступа
SCOPES: list[str] = ["https://www.googleapis.com/auth/drive.file"]


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
    """
    Создаёт и возвращает Google Drive API service.

    Использует OAuth 2.0 (Desktop App):
    - Если token.json существует — использует его.
    - Если токен истёк — автоматически обновляет.
    - Если token.json нет — открывает браузер для авторизации.
    """
    creds_path = BASE_DIR / GDRIVE_CREDENTIALS_PATH
    if not creds_path.exists():
        raise GDriveError(
            f"OAuth credentials не найдены: {creds_path}\n"
            "Скачайте OAuth Client ID (Desktop App) из Google Cloud Console "
            "и сохраните как credentials/gdrive_service.json"
        )

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        import httplib2

        creds = None

        # Загружаем сохранённый токен
        if TOKEN_PATH.exists():
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

        # Если токен невалиден — обновляем или авторизуемся заново
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("GDrive: обновление токена...")
                creds.refresh(Request())
            else:
                logger.info("GDrive: первичная авторизация через браузер...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(creds_path), SCOPES
                )
                creds = flow.run_local_server(port=0)

            # Сохраняем токен для будущих запусков
            TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
            logger.info(f"GDrive: токен сохранён в {TOKEN_PATH}")

        http = httplib2.Http(timeout=30)
        return build("drive", "v3", credentials=creds, http=http, num_retries=2)

    except ImportError:
        raise GDriveError(
            "google-api-python-client не установлен. "
            "Выполните: pip install google-api-python-client google-auth google-auth-oauthlib"
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

        # Сделать файл доступным по ссылке
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

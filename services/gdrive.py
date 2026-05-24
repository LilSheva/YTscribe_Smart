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
import re
from pathlib import Path
from dataclasses import dataclass

from core.config import (
    ENABLE_GDRIVE,
    GDRIVE_CREDENTIALS_PATH,
    GDRIVE_MEDIA_FOLDER_ID,
    GDRIVE_TRANSCRIPTS_FOLDER_ID,
    GDRIVE_MODE,
    BASE_DIR,
)
from core.exceptions import ServiceDisabledError, YTSBotError

logger = logging.getLogger(__name__)

# Путь к сохранённому токену (после первой авторизации)
TOKEN_PATH: Path = BASE_DIR / "credentials" / "token.json"

# Скоупы доступа
SCOPES: list[str] = ["https://www.googleapis.com/auth/drive.file"]

GDRIVE_FILE_ID_RE = re.compile(r"/file/d/([^/]+)")


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


def use_local_transcript_sync() -> bool:
    """Транскрипты пишутся в папку Google Drive Desktop, без Drive API."""
    return GDRIVE_MODE == "local"


def use_api_transcript_sync() -> bool:
    return ENABLE_GDRIVE and not use_local_transcript_sync()


def _local_transcript_result(file_path: Path) -> GDriveResult:
    if not file_path.exists():
        raise GDriveError(f"Файл не найден: {file_path}")
    size_mb = file_path.stat().st_size / (1024 * 1024)
    resolved = str(file_path.resolve())
    logger.info("Transcript in local Drive folder: %s (%.2f MB)", resolved, size_mb)
    return GDriveResult(
        file_name=file_path.name,
        file_id="local",
        public_url=resolved,
        size_mb=size_mb,
    )


def _sync_transcript_local_sync(file_path: Path) -> GDriveResult:
    _check_enabled()
    return _local_transcript_result(file_path)


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
        from google_auth_httplib2 import AuthorizedHttp

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

        http = AuthorizedHttp(creds, http=httplib2.Http(timeout=30))
        return build("drive", "v3", http=http, num_retries=2)

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


def extract_file_id(public_url: str) -> str | None:
    """Извлекает file_id из публичной ссылки Google Drive."""
    if not public_url:
        return None
    match = GDRIVE_FILE_ID_RE.search(public_url)
    return match.group(1) if match else None


def check_file_exists_sync(file_id: str) -> bool:
    """True если файл существует на Drive и не в корзине."""
    if not file_id:
        return False
    try:
        service = _get_service()
        meta = service.files().get(fileId=file_id, fields="id,trashed").execute()
        return not meta.get("trashed", False)
    except Exception as e:
        logger.debug(f"GDrive file check failed for {file_id}: {e}")
        return False


def _update_file_sync(file_path: Path, file_id: str) -> GDriveResult:
    """Обновляет содержимое существующего файла на Drive."""
    if not file_path.exists():
        raise GDriveError(f"Файл не найден: {file_path}")

    try:
        from googleapiclient.http import MediaFileUpload

        service = _get_service()
        media = MediaFileUpload(str(file_path), resumable=True)
        service.files().update(
            fileId=file_id,
            media_body=media,
            fields="id, name",
        ).execute()

        public_url = f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"
        size_mb = file_path.stat().st_size / (1024 * 1024)
        logger.info(f"Обновлён на GDrive: {file_path.name} ({size_mb:.1f} MB)")
        return GDriveResult(
            file_name=file_path.name,
            file_id=file_id,
            public_url=public_url,
            size_mb=size_mb,
        )
    except GDriveError:
        raise
    except Exception as e:
        raise GDriveError(f"Ошибка обновления на GDrive: {e}") from e


def upload_or_update_transcript_sync(file_path: Path, existing_url: str | None) -> GDriveResult:
    """Обновляет файл на Drive по URL или загружает новый."""
    file_id = extract_file_id(existing_url or "")
    if file_id and check_file_exists_sync(file_id):
        return _update_file_sync(file_path, file_id)
    return _upload_file_sync(file_path, GDRIVE_TRANSCRIPTS_FOLDER_ID)


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
    Асинхронно сохраняет/синхронизирует транскрипт (.md).

    local: файл уже в папке Drive Desktop.
    api: загрузка через Drive API.
    """
    _check_enabled()
    if use_local_transcript_sync():
        return await asyncio.to_thread(_sync_transcript_local_sync, file_path)
    logger.info(f"Загрузка транскрипта на GDrive: {file_path.name}")
    result = await asyncio.to_thread(_upload_file_sync, file_path, GDRIVE_TRANSCRIPTS_FOLDER_ID)
    return result


async def sync_transcript(file_path: Path, existing_url: str | None = None) -> GDriveResult:
    """Обновляет транскрипт в хранилище (локальная папка или Drive API)."""
    _check_enabled()
    if use_local_transcript_sync():
        return await asyncio.to_thread(_sync_transcript_local_sync, file_path)
    logger.info(f"Синхронизация транскрипта на GDrive: {file_path.name}")
    return await asyncio.to_thread(upload_or_update_transcript_sync, file_path, existing_url)


# Обратная совместимость (для url_handler)
async def upload(file_path: Path) -> GDriveResult:
    """Legacy-обёртка: определяет тип файла и загружает в нужную папку."""
    _check_enabled()
    if file_path.suffix == ".md":
        return await upload_transcript(file_path)
    if use_local_transcript_sync():
        raise GDriveError(
            "Медиа в local-режиме: задайте GDRIVE_MODE=api для m4a/mp4 или загрузите вручную."
        )
    return await upload_media(file_path)

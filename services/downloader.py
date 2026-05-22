"""
services/downloader.py — Асинхронный сервис загрузки медиа через yt-dlp.

Обеспечивает:
  - Получение метаданных (без скачивания файла).
  - Скачивание аудио (m4a) или видео (mp4) в temp/.
  - Проверку Feature Toggle перед каждой операцией.
  - Обёртку блокирующих вызовов в asyncio.to_thread().
"""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

import yt_dlp

from core.config import (
    ENABLE_DOWNLOADER,
    TEMP_DIR,
    BASE_DIR,
)
from services.cookie_manager import get_cookies_opts
from core.exceptions import ServiceDisabledError, DownloadError
from core.models import MediaTask

logger = logging.getLogger(__name__)

# Допустимые форматы (без конвертации)
SUPPORTED_FORMATS: dict[str, str] = {
    "m4a": "bestaudio[ext=m4a]/bestaudio",
    "mp4": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
}


def _check_enabled() -> None:
    """Проверяет, включён ли модуль загрузчика."""
    if not ENABLE_DOWNLOADER:
        raise ServiceDisabledError("DOWNLOADER")


def _base_opts() -> dict:
    """Базовые параметры yt-dlp (cookies, PO Token, тишина)."""
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
        "extractor_args": {
            "youtube": {
                "player_client": ["web", "mweb"],
            },
            # POT берём через HTTP-провайдер bgutil. IPv6, т.к. сервер биндится на [::]:4416
            # и на Windows IPv6-сокет не принимает IPv4-подключения.
            "youtubepot-bgutilhttp": {
                "base_url": ["http://[::1]:4416"],
            },
            # Принудительно отключаем script-провайдеры: их is_available() запускает
            # `deno run generate_once.ts --version` с таймаутом 15с, который висит
            # и валит инициализацию ещё до вызова HTTP-провайдера.
            "youtubepot-bgutilscript": {
                "server_home": ["C:/nonexistent_pot_path"],
            },
        },
    }
    opts.update(get_cookies_opts())
    return opts


def _extract_info(url: str) -> dict:
    """
    Синхронное извлечение метаданных (блокирующий вызов).

    Raises:
        DownloadError: Если yt-dlp не смог получить информацию.
    """
    opts = _base_opts()
    opts["skip_download"] = True

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                raise DownloadError(url, "yt-dlp вернул пустой результат")
            return info
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(url, str(e)) from e
    except Exception as e:
        raise DownloadError(url, f"Неожиданная ошибка: {e}") from e


def _download_file(url: str, format_type: str, on_progress: "Callable[[float], None] | None" = None) -> Path:
    """
    Синхронное скачивание файла (блокирующий вызов).

    Args:
        url: Ссылка на видео.
        format_type: Формат ("m4a" или "mp4").

    Returns:
        Path к скачанному файлу.

    Raises:
        DownloadError: При ошибке скачивания.
    """
    if format_type not in SUPPORTED_FORMATS:
        raise DownloadError(url, f"Неподдерживаемый формат: {format_type}")

    # Создаём temp/ если не существует
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    # Шаблон имени файла
    output_template = str(TEMP_DIR / "%(title)s.%(ext)s")

    opts = _base_opts()
    opts.update({
        "format": SUPPORTED_FORMATS[format_type],
        "outtmpl": output_template,
        "skip_download": False,
    })

    if on_progress:
        def _hook(d: dict) -> None:
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                downloaded = d.get("downloaded_bytes", 0)
                if total:
                    on_progress(downloaded / total)
            elif d.get("status") == "finished":
                on_progress(1.0)
        opts["progress_hooks"] = [_hook]

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise DownloadError(url, "yt-dlp вернул пустой результат после загрузки")

            # Определяем путь к скачанному файлу
            filename = ydl.prepare_filename(info)
            file_path = Path(filename)

            if not file_path.exists():
                # yt-dlp мог изменить расширение
                possible = list(TEMP_DIR.glob(f"{file_path.stem}.*"))
                if possible:
                    file_path = possible[0]
                else:
                    raise DownloadError(url, f"Файл не найден после загрузки: {filename}")

            logger.info(f"Скачан: {file_path.name} ({file_path.stat().st_size / 1024 / 1024:.1f} MB)")
            return file_path

    except DownloadError:
        raise
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(url, str(e)) from e
    except Exception as e:
        raise DownloadError(url, f"Неожиданная ошибка: {e}") from e


# ===== PUBLIC ASYNC API =====


async def get_info(url: str) -> MediaTask:
    """
    Асинхронно получает метаданные видео (без скачивания).

    Args:
        url: Ссылка на видео (YouTube, и др.).

    Returns:
        MediaTask с заполненными метаданными.

    Raises:
        ServiceDisabledError: Если модуль отключён.
        DownloadError: Если не удалось получить метаданные.
    """
    _check_enabled()
    logger.debug(f"Запрос метаданных: {url}")

    info = await asyncio.to_thread(_extract_info, url)

    task = MediaTask(
        url=url,
        title=info.get("title", "Неизвестно"),
        channel=info.get("channel", info.get("uploader", "Неизвестно")),
        channel_id=info.get("channel_id", ""),
        channel_url=info.get("channel_url", ""),
        duration_sec=int(info.get("duration", 0)),
        thumbnail_url=info.get("thumbnail", ""),
        view_count=info.get("view_count"),
        like_count=info.get("like_count"),
        comment_count=info.get("comment_count"),
        video_id=info.get("id", ""),
        upload_date=info.get("upload_date", ""),
        description=info.get("description", ""),
        categories=info.get("categories", []) or [],
        tags=info.get("tags", []) or [],
        language=info.get("language", ""),
        age_limit=int(info.get("age_limit", 0)),
        live_status=info.get("live_status", ""),
        availability=info.get("availability", ""),
        chapters=info.get("chapters", []) or [],
    )

    logger.info(f"Метаданные: [{task.channel}] {task.title} ({task.duration_formatted})")
    return task


async def download_media(url: str, format_type: str = "m4a", on_progress: Callable[[float], None] | None = None) -> MediaTask:
    """
    Асинхронно скачивает медиа-файл.

    Args:
        url: Ссылка на видео.
        format_type: Формат загрузки ("m4a" или "mp4").

    Returns:
        MediaTask с заполненным temp_file_path.

    Raises:
        ServiceDisabledError: Если модуль отключён.
        DownloadError: При ошибке скачивания.
    """
    _check_enabled()
    logger.info(f"Начало загрузки [{format_type}]: {url}")

    # Сначала получаем метаданные
    task = await get_info(url)

    # Затем скачиваем
    file_path = await asyncio.to_thread(_download_file, url, format_type, on_progress)
    task.temp_file_path = file_path

    logger.info(f"Загрузка завершена: {file_path.name} ({task.file_size_mb:.1f} MB)")
    return task

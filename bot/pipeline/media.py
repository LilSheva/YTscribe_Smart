"""Скачивание и транскрибация с прогрессом."""

from __future__ import annotations

import logging
from pathlib import Path

from aiogram.types import Message

from core.exceptions import DownloadError, ServiceDisabledError, TranscriptionError
from core.models import MediaTask
from services import downloader
from services import transcriber
from services import timing_stats
from services import user_settings
from bot.keyboards.main_menu import get_error_keyboard
from bot.ui.progress import ProgressReporter

logger = logging.getLogger(__name__)


def bind_transcribe_progress(prog: ProgressReporter):
    """Связывает callback transcriber с ProgressReporter."""

    def on_progress(event: str, detail: str) -> None:
        if event == "prepare":
            prog.set_stage("Подготовка", detail)
        elif event == "split":
            prog.set_stage("Нарезка аудио", detail)
        elif event == "chunk":
            prog.set_stage("Транскрибация", detail)
            if detail.startswith("Часть "):
                try:
                    part_info = detail.split(":")[0].replace("Часть ", "")
                    cur, tot = part_info.split("/")
                    prog.set_pct(int(cur) / int(tot))
                except (ValueError, IndexError):
                    pass
        elif event == "api":
            prog.set_stage("Speech-to-Text", detail)
        elif event == "api_fallback":
            prog.set_stage("Speech-to-Text (fallback)", detail)

    return on_progress


async def download_with_progress(
    message: Message,
    url: str,
    task: MediaTask,
    format_type: str,
    *,
    title: str | None = None,
    video_id: str | None = None,
) -> Path:
    """Скачивает медиа с детальным прогрессом yt-dlp."""
    display_title = title or f"⬇️ {task.title}"
    prog = ProgressReporter(message, title=display_title)
    await prog.start()
    prog.set_stage("Скачивание", f"yt-dlp: {format_type}")

    error_kb = (
        get_error_keyboard(
            retry_callback=f"retry:dl:{video_id}:{format_type}",
            back_callback=f"nav:card:{video_id}",
        )
        if video_id
        else None
    )

    def on_progress(pct: float) -> None:
        prog.set_pct(pct)
        sub = "Подключение к YouTube..." if pct <= 0 else f"Загрузка {pct * 100:.0f}%"
        prog.set_detail(f"yt-dlp: {format_type}", subdetail=sub)

    try:
        result_task = await downloader.download_media(
            url, format_type, on_progress=on_progress, task=task
        )
    except (ServiceDisabledError, DownloadError) as e:
        await prog.show_error("Скачивание", e.message, reply_markup=error_kb)
        raise
    except Exception as e:
        logger.error(f"Download error: {e}")
        await prog.show_error("Скачивание", "Ошибка при скачивании.", str(e), reply_markup=error_kb)
        raise
    finally:
        await prog.stop()

    file_path = result_task.temp_file_path
    if not file_path or not file_path.exists():
        await prog.show_error(
            "Скачивание",
            "Файл не найден после загрузки.",
            reply_markup=error_kb,
        )
        raise DownloadError(url, "Файл не найден после загрузки")
    return file_path


async def download_audio_with_progress(
    message: Message,
    url: str,
    task: MediaTask,
    prefix: str,
    *,
    video_id: str | None = None,
) -> Path:
    """Скачивает m4a с прогрессом."""
    title = prefix or f"⬇️ {task.title}"
    return await download_with_progress(
        message, url, task, "m4a", title=title, video_id=video_id
    )


async def transcribe_with_progress(
    message: Message,
    task: MediaTask,
    file_path: Path,
    user_id: int,
    *,
    video_id: str | None = None,
) -> str:
    """Транскрибирует файл с поэтапным прогрессом."""
    prefs = user_settings.get_user_settings(user_id)
    size_mb = file_path.stat().st_size / (1024 * 1024)
    lang_label = user_settings.language_label(prefs.transcribe_language)
    prog = ProgressReporter(message, title=f"📝 {task.title} ({size_mb:.1f} MB)")
    await prog.start()
    prog.set_stage(
        "Транскрибация",
        f"Модель: {prefs.whisper_model} • {lang_label}",
        stage_key=timing_stats.STAGE_TRANSCRIBE,
        context={
            "file_size_mb": round(size_mb, 1),
            "duration_sec": task.duration_sec,
        },
    )

    error_kb = (
        get_error_keyboard(
            retry_callback=f"retry:transcript:{video_id}",
            back_callback=f"nav:card:{video_id}",
        )
        if video_id
        else None
    )

    try:
        return await transcriber.transcribe(
            file_path,
            model=prefs.whisper_model,
            language=prefs.transcribe_language,
            on_progress=bind_transcribe_progress(prog),
        )
    except (ServiceDisabledError, TranscriptionError) as e:
        await prog.show_error("Транскрибация", e.message, reply_markup=error_kb)
        raise
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        await prog.show_error(
            "Транскрибация",
            "Ошибка транскрибации.",
            str(e),
            reply_markup=error_kb,
        )
        raise
    finally:
        await prog.stop()

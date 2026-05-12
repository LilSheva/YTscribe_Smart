"""
utils/media_chunker.py — Нарезка больших аудиофайлов через ffmpeg.

Используется когда файл превышает лимит Groq free tier (25 MB).
Нарезка на сегменты ~20 MB для безопасной отправки в API.
"""

import asyncio
import logging
import subprocess
from pathlib import Path

from core.config import WHISPER_MAX_FILE_MB

logger = logging.getLogger(__name__)

# Целевой размер чанка (чуть меньше лимита для запаса)
TARGET_CHUNK_MB: float = WHISPER_MAX_FILE_MB * 0.8  # ~20 MB при лимите 25


def _get_duration_sec(file_path: Path) -> float:
    """
    Получает длительность аудиофайла в секундах через ffprobe.

    Args:
        file_path: Путь к аудиофайлу.

    Returns:
        Длительность в секундах.

    Raises:
        RuntimeError: Если ffprobe не удалось определить длительность.
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe error: {result.stderr.strip()}")
        return float(result.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"Не удалось определить длительность: {e}") from e


def _split_audio_sync(file_path: Path, segment_duration_sec: int) -> list[Path]:
    """
    Синхронная нарезка аудио на сегменты через ffmpeg.

    Args:
        file_path: Путь к исходному аудиофайлу.
        segment_duration_sec: Длительность каждого сегмента в секундах.

    Returns:
        Список путей к сегментам.

    Raises:
        RuntimeError: Если ffmpeg не смог нарезать файл.
    """
    output_dir = file_path.parent
    stem = file_path.stem
    ext = file_path.suffix  # .m4a, .mp4 и т.д.

    # Паттерн имени: filename_chunk_001.m4a
    output_pattern = str(output_dir / f"{stem}_chunk_%03d{ext}")

    cmd = [
        "ffmpeg",
        "-i", str(file_path),
        "-f", "segment",
        "-segment_time", str(segment_duration_sec),
        "-c", "copy",  # Без перекодирования — быстро!
        "-y",  # Перезаписать если есть
        output_pattern,
    ]

    logger.debug(f"ffmpeg split command: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg error: {result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg timeout: нарезка заняла слишком много времени")

    # Собираем все чанки
    chunks = sorted(output_dir.glob(f"{stem}_chunk_*{ext}"))

    if not chunks:
        raise RuntimeError(f"ffmpeg не создал чанков для {file_path.name}")

    logger.info(f"Нарезано {len(chunks)} чанков из {file_path.name}")
    return chunks


async def split_audio(file_path: Path) -> list[Path]:
    """
    Асинхронно нарезает аудиофайл на сегменты, если он превышает лимит.

    Если файл меньше лимита — возвращает список с одним элементом (сам файл).

    Args:
        file_path: Путь к аудиофайлу.

    Returns:
        Список путей к сегментам (или [file_path] если нарезка не нужна).

    Raises:
        RuntimeError: При ошибке ffmpeg/ffprobe.
    """
    file_size_mb = file_path.stat().st_size / (1024 * 1024)

    if file_size_mb <= WHISPER_MAX_FILE_MB:
        logger.debug(f"Файл {file_path.name} ({file_size_mb:.1f} MB) не превышает лимит, нарезка не нужна")
        return [file_path]

    logger.info(
        f"Файл {file_path.name} ({file_size_mb:.1f} MB) превышает лимит "
        f"({WHISPER_MAX_FILE_MB} MB), начинаю нарезку..."
    )

    # Определяем длительность
    duration_sec = await asyncio.to_thread(_get_duration_sec, file_path)

    # Вычисляем длительность сегмента (пропорционально размеру)
    # Если файл 50MB и лимит 25MB → нужно 3 чанка по ~17MB (с запасом)
    ratio = TARGET_CHUNK_MB / file_size_mb
    segment_duration_sec = int(duration_sec * ratio)

    # Минимум 60 секунд, максимум — вся длительность
    segment_duration_sec = max(60, min(segment_duration_sec, int(duration_sec)))

    logger.debug(
        f"Duration: {duration_sec:.0f}s, ratio: {ratio:.2f}, "
        f"segment: {segment_duration_sec}s"
    )

    # Нарезаем
    chunks = await asyncio.to_thread(_split_audio_sync, file_path, segment_duration_sec)
    return chunks


def cleanup_chunks(chunks: list[Path], original: Path) -> None:
    """
    Удаляет временные чанки после транскрибации.

    Args:
        chunks: Список путей к чанкам.
        original: Оригинальный файл (не удаляется).
    """
    for chunk in chunks:
        if chunk != original and chunk.exists():
            try:
                chunk.unlink()
                logger.debug(f"Удалён чанк: {chunk.name}")
            except OSError as e:
                logger.warning(f"Не удалось удалить чанк {chunk.name}: {e}")

"""
core/models.py — Data Models проекта YTS_bot.

Типизированные структуры данных, которые передаются между слоями.
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MediaTask:
    """
    Результат парсинга ссылки — метаданные медиа.

    Attributes:
        url: Оригинальная ссылка на видео.
        title: Название видео.
        channel: Название канала.
        duration_sec: Длительность в секундах.
        temp_file_path: Путь к скачанному файлу (None до загрузки).
        thumbnail_url: Ссылка на превью (опционально).
    """

    url: str
    title: str = "Неизвестно"
    channel: str = "Неизвестно"
    duration_sec: int = 0
    temp_file_path: Path | None = None
    thumbnail_url: str = ""

    @property
    def duration_formatted(self) -> str:
        """Человекочитаемая длительность (HH:MM:SS или MM:SS)."""
        hours, remainder = divmod(self.duration_sec, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    @property
    def file_size_mb(self) -> float | None:
        """Размер скачанного файла в МБ (None если файл ещё не скачан)."""
        if self.temp_file_path and self.temp_file_path.exists():
            return self.temp_file_path.stat().st_size / (1024 * 1024)
        return None

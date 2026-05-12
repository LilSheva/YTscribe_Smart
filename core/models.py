"""
core/models.py — Data Models проекта YTS_bot.

Типизированные структуры данных, которые передаются между слоями.
"""

from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime


@dataclass
class MediaTask:
    """
    Результат парсинга ссылки — полные метаданные медиа.

    Заполняется из yt-dlp extract_info.
    """

    # Основные
    url: str
    title: str = "Неизвестно"
    channel: str = "Неизвестно"
    channel_id: str = ""
    channel_url: str = ""
    duration_sec: int = 0
    temp_file_path: Path | None = None
    thumbnail_url: str = ""

    # Статистика
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None

    # Даты и ID
    video_id: str = ""
    upload_date: str = ""  # YYYYMMDD
    description: str = ""

    # Категоризация
    categories: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    language: str = ""

    # Дополнительные
    age_limit: int = 0
    live_status: str = ""  # was_live, is_live, not_live
    availability: str = ""  # public, unlisted, private
    chapters: list[dict] = field(default_factory=list)  # [{title, start_time, end_time}]

    @property
    def duration_formatted(self) -> str:
        """Человекочитаемая длительность (HH:MM:SS или MM:SS)."""
        hours, remainder = divmod(self.duration_sec, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    @property
    def upload_date_formatted(self) -> str:
        """Дата публикации в формате YYYY-MM-DD."""
        if self.upload_date and len(self.upload_date) == 8:
            return f"{self.upload_date[:4]}-{self.upload_date[4:6]}-{self.upload_date[6:]}"
        return self.upload_date or "Неизвестно"

    @property
    def file_size_mb(self) -> float | None:
        """Размер скачанного файла в МБ (None если файл ещё не скачан)."""
        if self.temp_file_path and self.temp_file_path.exists():
            return self.temp_file_path.stat().st_size / (1024 * 1024)
        return None

    @property
    def view_count_formatted(self) -> str:
        """Форматированное число просмотров."""
        if self.view_count is None:
            return "—"
        if self.view_count >= 1_000_000:
            return f"{self.view_count / 1_000_000:.1f}M"
        if self.view_count >= 1_000:
            return f"{self.view_count / 1_000:.1f}K"
        return str(self.view_count)

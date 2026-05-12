"""
utils/md_generator.py — Генерация Markdown-документов из транскриптов.

Создаёт .md файлы с полными метаданными видео + текстом транскрипта
для последующей загрузки на Google Drive и ингеста в базу знаний.
"""

import logging
from datetime import datetime
from pathlib import Path

from core.config import TEMP_DIR
from core.models import MediaTask

logger = logging.getLogger(__name__)


def generate_transcript_md(task: MediaTask, transcript: str) -> Path:
    """
    Генерирует .md файл с метаданными видео и транскриптом.

    Args:
        task: MediaTask с заполненными метаданными.
        transcript: Полный текст транскрипта.

    Returns:
        Path к сгенерированному .md файлу в temp/.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines: list[str] = []

    # Заголовок
    lines.append(f"# {task.title}\n")

    # Метаданные
    lines.append("## Метаданные")
    lines.append(f"- **Канал:** {task.channel}")
    if task.channel_url:
        lines.append(f"- **Канал URL:** {task.channel_url}")
    lines.append(f"- **Дата публикации:** {task.upload_date_formatted}")
    lines.append(f"- **Длительность:** {task.duration_formatted}")

    if task.view_count is not None:
        lines.append(f"- **Просмотры:** {task.view_count:,}".replace(",", " "))
    if task.like_count is not None:
        lines.append(f"- **Лайки:** {task.like_count:,}".replace(",", " "))
    if task.comment_count is not None:
        lines.append(f"- **Комментарии:** {task.comment_count:,}".replace(",", " "))

    lines.append(f"- **URL:** {task.url}")

    if task.video_id:
        lines.append(f"- **Video ID:** {task.video_id}")

    if task.categories:
        lines.append(f"- **Категории:** {', '.join(task.categories)}")
    if task.tags:
        # Ограничиваем до 20 тегов для читаемости
        tags_display = task.tags[:20]
        lines.append(f"- **Теги:** {', '.join(tags_display)}")
        if len(task.tags) > 20:
            lines.append(f"  _(ещё {len(task.tags) - 20} тегов)_")

    if task.language:
        lines.append(f"- **Язык:** {task.language}")
    if task.live_status and task.live_status != "not_live":
        lines.append(f"- **Тип:** {task.live_status}")
    if task.age_limit:
        lines.append(f"- **Возрастное ограничение:** {task.age_limit}+")

    lines.append(f"- **Дата обработки:** {now}")
    lines.append("")

    # Главы (если есть)
    if task.chapters:
        lines.append("## Главы (Chapters)")
        for ch in task.chapters:
            start = _format_seconds(ch.get("start_time", 0))
            title = ch.get("title", "—")
            lines.append(f"- `{start}` — {title}")
        lines.append("")

    # Описание
    if task.description:
        lines.append("## Описание видео")
        # Ограничиваем описание 2000 символов
        desc = task.description[:2000]
        if len(task.description) > 2000:
            desc += "\n\n_(описание обрезано)_"
        lines.append(desc)
        lines.append("")

    # Разделитель
    lines.append("---\n")

    # Транскрипт
    lines.append("## Транскрипт")
    lines.append(transcript)
    lines.append("")

    # Собираем файл
    content = "\n".join(lines)

    # Создаём файл
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    safe_title = _sanitize_filename(task.title)
    md_filename = f"{safe_title}_transcript.md"
    md_path = TEMP_DIR / md_filename

    md_path.write_text(content, encoding="utf-8")
    logger.info(f"Сгенерирован .md: {md_filename} ({len(content)} символов)")

    return md_path


def _format_seconds(seconds: float | int) -> str:
    """Форматирует секунды в MM:SS или HH:MM:SS."""
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _sanitize_filename(title: str) -> str:
    """Очищает название для использования в имени файла."""
    # Убираем запрещённые символы
    forbidden = '<>:"/\\|?*'
    result = title
    for ch in forbidden:
        result = result.replace(ch, "")
    # Обрезаем до разумной длины
    return result[:80].strip()

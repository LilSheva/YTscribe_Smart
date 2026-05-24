"""
utils/url_parser.py — Извлечение и нормализация YouTube-ссылок из текста.
"""

import re

YOUTUBE_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?"
    r"(?:youtube\.com/(?:watch\?v=|shorts/|live/)|youtu\.be/)"
    r"([\w\-]{11})"
)


def extract_video_id(url: str) -> str | None:
    """Возвращает 11-символьный video_id или None."""
    match = YOUTUBE_URL_PATTERN.search(url)
    return match.group(1) if match else None


def normalize_youtube_url(url: str) -> str | None:
    """Канонический URL watch?v=ID."""
    video_id = extract_video_id(url)
    if not video_id:
        return None
    return f"https://www.youtube.com/watch?v={video_id}"


def parse_urls_from_text(text: str) -> list[str]:
    """
    Извлекает уникальные YouTube URL из текста (порядок первого появления).
    """
    seen: set[str] = set()
    result: list[str] = []
    for match in YOUTUBE_URL_PATTERN.finditer(text):
        video_id = match.group(1)
        if video_id in seen:
            continue
        seen.add(video_id)
        result.append(f"https://www.youtube.com/watch?v={video_id}")
    return result

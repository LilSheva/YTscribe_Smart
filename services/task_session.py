"""In-memory сессии видео (key=video_id) с rehydrate из БД после рестарта."""

from __future__ import annotations

import logging

from core.models import MediaTask
from services import db

logger = logging.getLogger(__name__)

_sessions: dict[str, dict] = {}


def register_session(video_id: str, session: dict) -> dict:
    """Регистрирует или обновляет сессию в памяти."""
    _sessions[video_id] = session
    return session


def get_session(video_id: str) -> dict | None:
    return _sessions.get(video_id)


def require_session(video_id: str, user_id: int) -> dict | None:
    """Сессия из памяти или восстановление из БД (rehydrate)."""
    existing = _sessions.get(video_id)
    if existing:
        if user_id and not existing.get("user_id"):
            existing["user_id"] = user_id
        return existing

    entry = db.get_video(video_id)
    if not entry:
        return None

    url = f"https://youtu.be/{video_id}"
    task = db.video_entry_to_task(entry, url)
    session: dict = {
        "task": task,
        "url": url,
        "user_id": user_id,
    }
    text = db.get_transcript_text(video_id)
    if text:
        session["transcript"] = text
    variants = db.get_variants(video_id)
    if variants:
        session["variants"] = variants

    _sessions[video_id] = session
    logger.debug("Rehydrated session for %s (user=%s)", video_id, user_id)
    return session


def load_transcript(session: dict) -> str | None:
    """Транскрипт из памяти сессии или из файла/БД."""
    text = session.get("transcript")
    if text:
        return text
    task: MediaTask = session["task"]
    text = db.get_transcript_text(task.video_id)
    if text:
        session["transcript"] = text
    return text


def sessions_for_user(user_id: int):
    for session in _sessions.values():
        if session.get("user_id") == user_id:
            yield session

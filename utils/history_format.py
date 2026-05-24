"""Форматирование записей истории для UI."""

from __future__ import annotations

from datetime import datetime

from services.db import VideoEntry


def format_added_at_short(added_at: str) -> str:
    if not added_at:
        return "—"
    try:
        dt = datetime.fromisoformat(added_at.replace("Z", "+00:00")[:19])
        return dt.strftime("%d.%m %H:%M")
    except ValueError:
        return added_at[:10]


def format_history_list_label(entry: VideoEntry, *, index: int) -> str:
    """Подпись кнопки: «3. 22.05 18:44 — Название (15м)»."""
    dt = format_added_at_short(entry.added_at)
    mins = entry.duration_sec // 60
    title = entry.title[:32] + ("…" if len(entry.title) > 32 else "")
    return f"{index}. {dt} — {title} ({mins}м)"


def format_history_item_header(entry: VideoEntry) -> str:
    dt = format_added_at_short(entry.added_at)
    mins = entry.duration_sec // 60
    return (
        f"🎬 <b>{entry.title}</b>\n"
        f"📺 {entry.channel} • {mins} мин\n"
        f"🕐 Транскрибировано: {dt}"
    )

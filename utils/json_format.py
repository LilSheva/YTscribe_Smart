"""Каноническая JSON-схема v1.0 для транскриптов и AI-анализа."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from core.models import MediaTask

SCHEMA_VERSION = "1.0"
SOURCE = "ytscribe_smart"
DEFAULT_CONTEXT_POLICY = "retrieval"

FORBIDDEN_FILENAME_CHARS = '<>:"/\\|?*'


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sanitize_filename_title(title: str) -> str:
    result = title
    for ch in FORBIDDEN_FILENAME_CHARS:
        result = result.replace(ch, "")
    return result.strip()


def transcript_filename(video_id: str, title: str) -> str:
    return f"{video_id}_{sanitize_filename_title(title)}.json"


def empty_chat() -> dict[str, Any]:
    return {
        "context_policy": DEFAULT_CONTEXT_POLICY,
        "messages": [],
        "updated_at": None,
    }


def build_video_block(task: MediaTask, *, added_at: str = "", added_by_user_id: int = 0) -> dict[str, Any]:
    return {
        "video_id": task.video_id,
        "title": task.title,
        "url": task.url,
        "channel": task.channel,
        "duration_sec": task.duration_sec,
        "upload_date": task.upload_date_formatted if task.upload_date else "",
        "language": task.language or "",
        "added_at": added_at or _now_iso(),
        "added_by_user_id": added_by_user_id,
    }


def build_document(
    task: MediaTask,
    transcript_text: str,
    *,
    added_by_user_id: int = 0,
    added_at: str = "",
    processed_at: str | None = None,
    ai_analysis: list[dict[str, Any]] | None = None,
    sync: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "video": build_video_block(task, added_at=added_at, added_by_user_id=added_by_user_id),
        "transcript": {
            "text": transcript_text.strip(),
            "processed_at": processed_at or _now_iso(),
        },
        "ai_analysis": ai_analysis or [],
        "sync": sync or {"gdrive_path": "", "gdrive_synced_at": None},
    }


def build_ai_entry(
    entry_id: int,
    label: str,
    prompt: str,
    body: str,
    *,
    model: str,
    created_at: str | None = None,
    chat: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "label": label.strip(),
        "prompt": prompt.strip(),
        "model": model,
        "created_at": created_at or _now_iso(),
        "body": body.strip(),
        "chat": chat or empty_chat(),
    }


def build_chat_message(
    message_id: int,
    role: str,
    content: str,
    *,
    model: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "id": message_id,
        "role": role,
        "content": content.strip(),
        "created_at": created_at or _now_iso(),
    }
    if role == "assistant" and model:
        msg["model"] = model
    return msg


def save_document(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_document(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_document(doc: dict[str, Any]) -> list[str]:
    issues: list[str] = []

    if doc.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"schema_version must be {SCHEMA_VERSION}")

    video = doc.get("video")
    if not isinstance(video, dict) or not video.get("video_id"):
        issues.append("video.video_id required")

    transcript = doc.get("transcript")
    if not isinstance(transcript, dict) or not str(transcript.get("text", "")).strip():
        issues.append("transcript.text required")

    ai_list = doc.get("ai_analysis")
    if not isinstance(ai_list, list):
        issues.append("ai_analysis must be array")
        return issues

    seen_ids: set[int] = set()
    for entry in ai_list:
        if not isinstance(entry, dict):
            issues.append("ai_analysis entry must be object")
            continue
        eid = entry.get("id")
        if not isinstance(eid, int) or eid in seen_ids:
            issues.append(f"duplicate or invalid ai_analysis.id: {eid}")
        seen_ids.add(eid)
        if not entry.get("label") or not entry.get("body"):
            issues.append(f"ai_analysis[{eid}]: label and body required")
        chat = entry.get("chat")
        if not isinstance(chat, dict):
            issues.append(f"ai_analysis[{eid}]: chat required")
            continue
        if chat.get("context_policy") not in ("retrieval", "summary_only", "full_transcript"):
            issues.append(f"ai_analysis[{eid}]: invalid context_policy")
        messages = chat.get("messages")
        if not isinstance(messages, list):
            issues.append(f"ai_analysis[{eid}]: chat.messages must be array")

    return issues


def ai_entry_count(doc: dict[str, Any]) -> int:
    ai = doc.get("ai_analysis")
    return len(ai) if isinstance(ai, list) else 0


def find_ai_entry(doc: dict[str, Any], analysis_id: int) -> dict[str, Any] | None:
    for entry in doc.get("ai_analysis") or []:
        if entry.get("id") == analysis_id:
            return entry
    return None


def next_chat_message_id(entry: dict[str, Any]) -> int:
    messages = entry.get("chat", {}).get("messages") or []
    if not messages:
        return 1
    return max(m.get("id", 0) for m in messages) + 1

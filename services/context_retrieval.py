"""Подбор фрагментов транскрипта для контекста чата (retrieval)."""

from __future__ import annotations

import re
from typing import Literal

ContextPolicy = Literal["retrieval", "summary_only", "full_transcript"]

DEFAULT_MAX_CHUNKS = 3
DEFAULT_CHUNK_CHARS = 1800


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]{3,}", text.lower())
    return set(words)


def split_transcript_chunks(
    transcript: str,
    *,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
) -> list[str]:
    """Делит транскрипт на фрагменты по абзацам/предложениям."""
    text = transcript.strip()
    if not text:
        return []
    if len(text) <= chunk_chars:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for part in re.split(r"(?<=[.!?…])\s+|\n+", text):
        part = part.strip()
        if not part:
            continue
        if current_len + len(part) + 1 > chunk_chars and current:
            chunks.append(" ".join(current))
            current = [part]
            current_len = len(part)
        else:
            current.append(part)
            current_len += len(part) + 1

    if current:
        chunks.append(" ".join(current))
    return chunks


def score_chunk(chunk: str, query_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    chunk_tokens = _tokenize(chunk)
    if not chunk_tokens:
        return 0.0
    overlap = query_tokens & chunk_tokens
    return len(overlap) / len(query_tokens)


def retrieve_transcript_chunks(
    transcript: str,
    query: str,
    *,
    max_chunks: int = DEFAULT_MAX_CHUNKS,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
) -> list[str]:
    """Возвращает top-N фрагментов транскрипта, релевантных запросу."""
    chunks = split_transcript_chunks(transcript, chunk_chars=chunk_chars)
    if not chunks:
        return []
    if len(chunks) <= max_chunks:
        return chunks

    query_tokens = _tokenize(query)
    if not query_tokens:
        return chunks[:max_chunks]

    ranked = sorted(chunks, key=lambda c: score_chunk(c, query_tokens), reverse=True)
    return ranked[:max_chunks]


def build_chat_context(
    *,
    policy: ContextPolicy,
    transcript: str,
    analysis_body: str,
    chat_messages: list[dict],
    user_message: str,
    max_chunks: int = DEFAULT_MAX_CHUNKS,
) -> str:
    """
    Собирает текст контекста для LLM в зависимости от context_policy.
    """
    parts: list[str] = []

    parts.append("--- АНАЛИЗ (тема) ---")
    parts.append(analysis_body.strip())

    history = chat_messages or []
    if history:
        parts.append("\n--- ИСТОРИЯ ЧАТА ---")
        for msg in history[-12:]:
            role = msg.get("role", "user")
            label = "Пользователь" if role == "user" else "Ассистент"
            parts.append(f"{label}: {msg.get('content', '').strip()}")

    if policy == "full_transcript":
        parts.append("\n--- ТРАНСКРИПТ (полный) ---")
        parts.append(transcript.strip())
    elif policy == "retrieval":
        chunks = retrieve_transcript_chunks(
            transcript,
            user_message,
            max_chunks=max_chunks,
        )
        if chunks:
            parts.append("\n--- ФРАГМЕНТЫ ТРАНСКРИПТА ---")
            for i, chunk in enumerate(chunks, start=1):
                parts.append(f"[{i}]\n{chunk}")

    parts.append(f"\n--- ВОПРОС ---\n{user_message.strip()}")
    return "\n".join(parts)

"""Чат по теме ai_analysis с retrieval-контекстом."""

from __future__ import annotations

import logging

from core.config import ENABLE_LLM, LLM_MODEL
from services import llm_router
from services.context_retrieval import ContextPolicy, build_chat_context
from services.llm_persist import sync_transcript_storage
from services.transcript_json import append_chat_message, load_video_document
from utils.json_format import find_ai_entry

logger = logging.getLogger(__name__)

CHAT_SYSTEM_PROMPT = (
    "Ты продолжаешь обсуждение видео по уже готовому анализу. "
    "Опирайся на фрагменты транскрипта и анализ; не выдумывай факты. "
    "Ответ в markdown, только подзаголовки ####, без # и ##."
)


def get_chat_turn_count(video_id: str, analysis_id: int) -> int:
    loaded = load_video_document(video_id)
    if not loaded:
        return 0
    _, doc = loaded
    entry = find_ai_entry(doc, analysis_id)
    if not entry:
        return 0
    messages = entry.get("chat", {}).get("messages") or []
    return len([m for m in messages if m.get("role") == "user"])


async def run_analysis_chat(
    video_id: str,
    analysis_id: int,
    user_message: str,
    *,
    user_id: int,
    model: str | None = None,
    policy: ContextPolicy | None = None,
) -> str:
    """
    Отправляет сообщение в чат по теме analysis_id.
    Сохраняет user/assistant в JSON; синхронизирует Drive.
    """
    if not ENABLE_LLM:
        raise RuntimeError("LLM отключён")

    loaded = load_video_document(video_id)
    if not loaded:
        raise FileNotFoundError(f"JSON транскрипт не найден: {video_id}")

    _, doc = loaded
    entry = find_ai_entry(doc, analysis_id)
    if not entry:
        raise ValueError(f"ai_analysis id={analysis_id} не найден")

    chat = entry.get("chat") or {}
    effective_policy: ContextPolicy = policy or chat.get("context_policy") or "retrieval"
    transcript = doc.get("transcript", {}).get("text", "")
    body = entry.get("body", "")
    messages = chat.get("messages") or []

    context = build_chat_context(
        policy=effective_policy,
        transcript=transcript,
        analysis_body=body,
        chat_messages=messages,
        user_message=user_message,
    )

    llm_model = model or LLM_MODEL
    answer = await llm_router.analyze(
        context,
        user_prompt="Ответь на вопрос пользователя по контексту выше.",
        system_prompt=CHAT_SYSTEM_PROMPT,
        model=llm_model,
    )

    append_chat_message(video_id, analysis_id, "user", user_message)
    append_chat_message(
        video_id,
        analysis_id,
        "assistant",
        answer,
        model=llm_model,
    )
    await sync_transcript_storage(video_id)
    logger.info("Analysis chat OK: %s analysis=%s user=%s", video_id, analysis_id, user_id)
    return answer

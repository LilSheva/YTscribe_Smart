"""Авто-саммари сразу после транскрибации."""

from __future__ import annotations

import logging
from typing import Any

from core.config import AUTO_LLM_SUMMARY, ENABLE_LLM, OMNIROUTE_BASE_URL
from core.models import MediaTask
from services import db
from services import llm_router
from services import timing_stats
from services import user_settings
from services.llm_persist import persist_llm_result

logger = logging.getLogger(__name__)


async def run_auto_summary_if_enabled(
    task: MediaTask,
    transcript_text: str,
    user_id: int,
    message: Any | None = None,
) -> bool:
    """
    Запускает дефолтное саммари после транскрипта.
    Возвращает True если анализ выполнен или уже был.
    """
    if not AUTO_LLM_SUMMARY or not ENABLE_LLM:
        return False
    if not transcript_text.strip():
        return False

    state = db.get_state(task.video_id)
    if state and state.has_summary:
        logger.info("Auto summary skip (already has summary): %s", task.video_id)
        return True

    prefs = user_settings.get_user_settings(user_id)
    label = llm_router.AUTO_SUMMARY_LABEL
    prompt = llm_router.AUTO_SUMMARY_PROMPT

    prog = None
    if message:
        from bot.ui.progress import ProgressReporter

        prog = ProgressReporter(message, title=f"🧠 {label}: {task.title[:40]}")
        await prog.start()
        prog.set_stage(
            "AI: авто-саммари",
            f"POST {OMNIROUTE_BASE_URL}/chat/completions",
            subdetail=f"Модель: {prefs.llm_model}",
            stage_key=timing_stats.STAGE_LLM_ANALYZE,
            context={"transcript_chars": len(transcript_text)},
        )
        await prog.push()

    try:
        result = await llm_router.analyze(
            transcript_text,
            user_prompt=prompt,
            model=prefs.llm_model,
        )
        await persist_llm_result(task.video_id, label, prompt, result, model=prefs.llm_model)
        logger.info("Auto summary OK: %s (%s chars)", task.title, len(result))
        return True
    except Exception as e:
        logger.warning("Auto summary failed for %s: %s", task.video_id, e)
        if prog:
            await prog.show_error("AI: авто-саммари", str(e)[:200])
        return False
    finally:
        if prog:
            await prog.stop()

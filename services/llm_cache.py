"""Локальный кеш ответов LLM (одинаковый транскрипт + промпт + модель)."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from core.config import DATA_DIR, LLM_CACHE_ENABLED, LLM_CACHE_MAX_ENTRIES

logger = logging.getLogger(__name__)

CACHE_DIR = DATA_DIR / "llm_cache"


def _cache_key(*, model: str, system_prompt: str, user_prompt: str, text: str) -> str:
    payload = json.dumps(
        {"m": model, "s": system_prompt, "p": user_prompt, "t": text},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_cached(*, model: str, system_prompt: str, user_prompt: str, text: str) -> str | None:
    if not LLM_CACHE_ENABLED:
        return None
    key = _cache_key(model=model, system_prompt=system_prompt, user_prompt=user_prompt, text=text)
    path = CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        logger.info("LLM cache HIT: model=%s prompt=%r len=%s", model, user_prompt[:40], len(text))
        return data.get("result", "")
    except Exception as e:
        logger.warning("LLM cache read error: %s", e)
        return None


def set_cached(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    text: str,
    result: str,
) -> None:
    if not LLM_CACHE_ENABLED:
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = _cache_key(model=model, system_prompt=system_prompt, user_prompt=user_prompt, text=text)
    path = CACHE_DIR / f"{key}.json"
    path.write_text(
        json.dumps({"result": result}, ensure_ascii=False),
        encoding="utf-8",
    )
    _trim_cache()


def _trim_cache() -> None:
    files = sorted(CACHE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[LLM_CACHE_MAX_ENTRIES:]:
        try:
            old.unlink(missing_ok=True)
        except Exception:
            pass


def clear_cache() -> int:
    """Удаляет все файлы LLM-кеша. Возвращает число удалённых."""
    if not CACHE_DIR.exists():
        return 0
    removed = 0
    for path in CACHE_DIR.glob("*.json"):
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except Exception:
            pass
    logger.info("LLM cache cleared: %s files", removed)
    return removed

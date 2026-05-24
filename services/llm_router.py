"""
services/llm_router.py — Сервис LLM-аналитики через OmniRoute.

Отправляет транскрипт + промпт пользователя в OpenAI-совместимый endpoint
(OmniRoute → Claude 3.5 / Haiku / другие модели).

Обеспечивает:
  - Feature Toggle проверку.
  - Graceful degradation (ошибка API не крашит бота).
  - Настраиваемую модель и max_tokens из конфига.
"""

import logging

import httpx

from core.config import (
    ENABLE_LLM,
    OMNIROUTE_API_KEY,
    OMNIROUTE_BASE_URL,
    LLM_MODEL,
    LLM_MAX_TOKENS,
)
from core.exceptions import ServiceDisabledError, LLMError

logger = logging.getLogger(__name__)

# Системный промпт для анализа видео-контента
DEFAULT_SYSTEM_PROMPT: str = (
    "Ты — AI-ассистент для анализа видеоконтента. "
    "Тебе предоставлен транскрипт видео с YouTube. "
    "Отвечай на русском языке, если не указано иное. "
    "Будь точен, структурирован и полезен. "
    "Форматирование ответа: используй списки и подзаголовки ####. "
    "НЕ используй в ответе заголовки # и ## — они зарезервированы структурой документа."
)

# Лимит Telegram-сообщения
TELEGRAM_MSG_LIMIT: int = 4096

# Пресеты LLM (id → подпись в настройках)
PRESET_LLM_MODELS: dict[str, str] = {
    "claude-3-5-sonnet-20241022": "Claude 3.5 Sonnet",
    "anthropic/claude-sonnet-4": "Claude Sonnet 4",
    "google/gemini-2.0-flash-001": "Gemini 2.0 Flash",
    "openai/gpt-4o-mini": "GPT-4o Mini",
}


def get_available_models() -> dict[str, str]:
    """Модели LLM для меню настроек (+ default из .env)."""
    models = dict(PRESET_LLM_MODELS)
    if LLM_MODEL not in models:
        models[LLM_MODEL] = f"Default ({LLM_MODEL})"
    return models


def _check_enabled() -> None:
    """Проверяет, включён ли модуль LLM."""
    if not ENABLE_LLM:
        raise ServiceDisabledError("LLM")


def _validate_api_key() -> None:
    """Проверяет наличие API-ключа."""
    if not OMNIROUTE_API_KEY or OMNIROUTE_API_KEY.startswith("sk-x"):
        raise LLMError("OMNIROUTE_API_KEY не настроен. Заполните .env файл.")


async def analyze(
    text: str,
    user_prompt: str = "Сделай подробное саммари этого видео.",
    model: str | None = None,
    max_tokens: int | None = None,
    system_prompt: str | None = None,
) -> str:
    """
    Отправляет транскрипт + промпт в LLM и возвращает ответ.

    Args:
        text: Транскрипт видео (сырой текст).
        user_prompt: Промпт пользователя (что сделать с текстом).
        model: Модель LLM (по умолчанию из конфига).
        max_tokens: Лимит токенов ответа (по умолчанию из конфига).
        system_prompt: Кастомный системный промпт (по умолчанию DEFAULT_SYSTEM_PROMPT).

    Returns:
        Текст ответа LLM.

    Raises:
        ServiceDisabledError: Если модуль отключён.
        LLMError: При ошибке API.
    """
    _check_enabled()
    _validate_api_key()

    if not text.strip():
        raise LLMError("Пустой транскрипт — нечего анализировать.")

    model = model or LLM_MODEL
    max_tokens = max_tokens or LLM_MAX_TOKENS
    system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT

    from services.llm_cache import get_cached, set_cached

    cached = get_cached(
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        text=text,
    )
    if cached is not None:
        return cached

    # Формируем сообщение пользователя: промпт + транскрипт
    user_message = (
        f"{user_prompt}\n\n"
        f"--- ТРАНСКРИПТ ВИДЕО ---\n\n"
        f"{text}"
    )

    url = f"{OMNIROUTE_BASE_URL}/chat/completions"

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }

    headers = {
        "Authorization": f"Bearer {OMNIROUTE_API_KEY}",
        "Content-Type": "application/json",
    }

    logger.info(
        f"LLM запрос: model={model}, text_len={len(text)}, "
        f"prompt='{user_prompt[:50]}...'"
    )

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, json=payload, headers=headers)

        if response.status_code == 200:
            data = response.json()
            result = data["choices"][0]["message"]["content"]
            result = result.strip()
            set_cached(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                text=text,
                result=result,
            )
            logger.info(f"LLM ответ получен: {len(result)} символов")
            return result
        elif response.status_code == 413:
            raise LLMError(
                "Текст слишком длинный для выбранной модели. "
                "Попробуйте более короткое видео."
            )
        elif response.status_code == 429:
            raise LLMError("Превышен лимит запросов к API. Попробуйте позже.")
        else:
            error_detail = response.text[:300]
            raise LLMError(
                f"API вернул ошибку {response.status_code}: {error_detail}"
            )

    except httpx.TimeoutException:
        raise LLMError(
            "Timeout при обращении к LLM API. Модель обрабатывает запрос слишком долго."
        )
    except httpx.HTTPError as e:
        raise LLMError(f"HTTP ошибка при обращении к LLM: {e}")
    except KeyError:
        raise LLMError("Неожиданный формат ответа от API.")


def split_for_telegram(text: str, limit: int = TELEGRAM_MSG_LIMIT) -> list[str]:
    """
    Разбивает длинный текст на сообщения для Telegram (по лимиту 4096 символов).

    Старается разбивать по абзацам, а не посреди слова.

    Args:
        text: Исходный текст.
        limit: Максимальная длина одного сообщения.

    Returns:
        Список строк, каждая <= limit символов.
    """
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    current = ""

    for line in text.split("\n"):
        # Если текущий + новая строка влезают — добавляем
        if len(current) + len(line) + 1 <= limit:
            current += line + "\n"
        else:
            # Если текущий непустой — сохраняем
            if current.strip():
                parts.append(current.strip())
            # Если одна строка длиннее лимита — режем по словам
            if len(line) > limit:
                words = line.split(" ")
                current = ""
                for word in words:
                    if len(current) + len(word) + 1 <= limit:
                        current += word + " "
                    else:
                        if current.strip():
                            parts.append(current.strip())
                        current = word + " "
            else:
                current = line + "\n"

    if current.strip():
        parts.append(current.strip())

    return parts


# Промпт для мета-запроса — просим LLM предложить варианты анализа
_META_PROMPT = """Проанализируй транскрипт видео и предложи ровно 4 варианта его обработки.
Вариант 1 ВСЕГДА: название "Саммари с инсайтами и выводом", промпт "Сделай структурированное саммари. Подзаголовки только ####. 5-7 тезисов, инсайты, вывод. Не используй # и ##."
Варианты 2-4 — предложи сам исходя из содержания видео.

Требования к названиям: 4-8 слов, конкретно описывают ЧТО будет в ответе (не просто "Анализ" а "Разбор бизнес-модели с плюсами и минусами").
Требования к промптам: конкретная инструкция для LLM, 1-2 предложения.

Ответь СТРОГО в формате (ничего лишнего до и после блока):
===VARIANTS===
1|<название 4-8 слов>|<промпт-инструкция>
2|<название 4-8 слов>|<промпт-инструкция>
3|<название 4-8 слов>|<промпт-инструкция>
4|<название 4-8 слов>|<промпт-инструкция>
===END==="""


def parse_variants(response: str) -> list[dict] | None:
    """Парсит варианты анализа из ответа LLM. Возвращает None если формат нарушен."""
    import re
    match = re.search(r"===VARIANTS===(.*?)===END===", response, re.DOTALL)
    if not match:
        return None
    variants = []
    for line in match.group(1).strip().splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3 and parts[0].strip().isdigit():
            variants.append({"idx": int(parts[0].strip()), "label": parts[1].strip(), "prompt": parts[2].strip()})
    return variants if len(variants) == 4 else None


FALLBACK_VARIANT = {
    "idx": 1,
    "label": "Саммари с инсайтами и выводом",
    "prompt": (
        "Сделай структурированное саммари. "
        "Подзаголовки только #### (например #### Ключевые тезисы, #### Инсайты, #### Вывод). "
        "5-7 тезисов списком, неочевидные инсайты, итоговый вывод одним абзацем. "
        "Не используй # и ##."
    ),
}

AUTO_SUMMARY_LABEL: str = FALLBACK_VARIANT["label"]
AUTO_SUMMARY_PROMPT: str = FALLBACK_VARIANT["prompt"]


async def get_analysis_variants(
    text: str,
    extra_prompt: str = "",
    model: str | None = None,
) -> list[dict]:
    """Запрашивает у LLM варианты анализа. При ошибке возвращает [FALLBACK_VARIANT]."""
    _check_enabled()
    _validate_api_key()
    prompt = _META_PROMPT
    if extra_prompt:
        prompt = f"{_META_PROMPT}\n\nДополнительное уточнение от пользователя: {extra_prompt}"
    try:
        raw = await analyze(
            text=text[:8000],
            user_prompt=prompt,
            system_prompt="Ты помощник для анализа видеоконтента. Отвечай строго по формату.",
            max_tokens=600,
            model=model,
        )
        variants = parse_variants(raw)
        if variants:
            return variants
    except Exception as e:
        logger.warning(f"get_analysis_variants failed: {e}")
    return [FALLBACK_VARIANT]

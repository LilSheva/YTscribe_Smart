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
    "Используй Markdown для форматирования (заголовки, списки, выделение)."
)

# Лимит Telegram-сообщения
TELEGRAM_MSG_LIMIT: int = 4096


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
            logger.info(f"LLM ответ получен: {len(result)} символов")
            return result.strip()
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

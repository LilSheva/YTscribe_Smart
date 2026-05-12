"""
services/transcriber.py — Сервис транскрибации аудио (Speech-to-Text).

Стратегия:
  1. Основной провайдер: Groq (бесплатно, 216x realtime).
  2. Fallback: OmniRoute (через OpenAI-совместимый endpoint).
  3. Поддержка выбора модели (whisper-large-v3, v3-turbo, distil-whisper).
  4. Автоматическая нарезка файлов > 25MB через ffmpeg.

Оптимизировано для русского и английского контента.
"""

import asyncio
import logging
from pathlib import Path

import httpx

from core.config import (
    ENABLE_TRANSCRIPT,
    GROQ_API_KEY,
    OMNIROUTE_API_KEY,
    OMNIROUTE_BASE_URL,
    TRANSCRIPTION_PROVIDER,
    WHISPER_MODEL,
    WHISPER_MAX_FILE_MB,
)
from core.exceptions import ServiceDisabledError, TranscriptionError
from utils.media_chunker import split_audio, cleanup_chunks

logger = logging.getLogger(__name__)

# Доступные модели для выбора пользователем
AVAILABLE_MODELS: dict[str, str] = {
    "whisper-large-v3": "Whisper V3 (точная, RU+EN)",
    "whisper-large-v3-turbo": "Whisper V3 Turbo (быстрая, RU+EN)",
    "distil-whisper-large-v3-en": "Distil-Whisper (только EN, самая быстрая)",
}

# Провайдеры и их базовые URL
PROVIDERS: dict[str, dict[str, str]] = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "name": "Groq",
    },
    "omniroute": {
        "base_url": OMNIROUTE_BASE_URL,
        "name": "OmniRoute",
    },
}


def _check_enabled() -> None:
    """Проверяет, включён ли модуль транскрибации."""
    if not ENABLE_TRANSCRIPT:
        raise ServiceDisabledError("TRANSCRIPT")


def _get_api_key(provider: str) -> str:
    """Возвращает API-ключ для указанного провайдера."""
    if provider == "groq":
        if not GROQ_API_KEY or GROQ_API_KEY.startswith("gsk_x"):
            raise TranscriptionError("GROQ_API_KEY не настроен")
        return GROQ_API_KEY
    elif provider == "omniroute":
        if not OMNIROUTE_API_KEY or OMNIROUTE_API_KEY.startswith("sk-x"):
            raise TranscriptionError("OMNIROUTE_API_KEY не настроен")
        return OMNIROUTE_API_KEY
    else:
        raise TranscriptionError(f"Неизвестный провайдер: {provider}")


async def _transcribe_single_file(
    file_path: Path,
    model: str,
    provider: str,
) -> str:
    """
    Транскрибирует один аудиофайл через указанного провайдера.

    Args:
        file_path: Путь к аудиофайлу (должен быть <= WHISPER_MAX_FILE_MB).
        model: Название модели Whisper.
        provider: Провайдер ("groq" или "omniroute").

    Returns:
        Транскрибированный текст.

    Raises:
        TranscriptionError: При ошибке API.
    """
    provider_config = PROVIDERS.get(provider)
    if not provider_config:
        raise TranscriptionError(f"Провайдер '{provider}' не найден")

    api_key = _get_api_key(provider)
    base_url = provider_config["base_url"]
    url = f"{base_url}/audio/transcriptions"

    logger.debug(
        f"Транскрибация: {file_path.name} → {provider_config['name']} ({model})"
    )

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            with open(file_path, "rb") as audio_file:
                files = {"file": (file_path.name, audio_file, "audio/mpeg")}
                data = {
                    "model": model,
                    "language": "ru",  # Авто-определение, подсказка для RU
                    "response_format": "text",
                }
                headers = {"Authorization": f"Bearer {api_key}"}

                response = await client.post(
                    url,
                    files=files,
                    data=data,
                    headers=headers,
                )

        if response.status_code == 200:
            text = response.text.strip()
            logger.debug(f"Транскрибация OK: {len(text)} символов")
            return text
        else:
            error_detail = response.text[:500]
            raise TranscriptionError(
                f"{provider_config['name']} вернул ошибку {response.status_code}: {error_detail}"
            )

    except httpx.TimeoutException:
        raise TranscriptionError(
            f"Timeout при обращении к {provider_config['name']} ({url})"
        )
    except httpx.HTTPError as e:
        raise TranscriptionError(
            f"HTTP ошибка ({provider_config['name']}): {e}"
        )


async def _transcribe_with_fallback(
    file_path: Path,
    model: str,
) -> str:
    """
    Транскрибирует файл с fallback: Groq → OmniRoute.

    Args:
        file_path: Путь к аудиофайлу.
        model: Модель Whisper.

    Returns:
        Текст транскрибации.

    Raises:
        TranscriptionError: Если оба провайдера не сработали.
    """
    primary = TRANSCRIPTION_PROVIDER
    fallback = "omniroute" if primary == "groq" else "groq"

    # Попытка 1: основной провайдер
    try:
        logger.info(f"Транскрибация через {PROVIDERS[primary]['name']}...")
        return await _transcribe_single_file(file_path, model, primary)
    except TranscriptionError as e:
        logger.warning(f"Основной провайдер ({primary}) не сработал: {e.message}")

    # Попытка 2: fallback
    try:
        logger.info(f"Fallback: транскрибация через {PROVIDERS[fallback]['name']}...")
        return await _transcribe_single_file(file_path, model, fallback)
    except TranscriptionError as e:
        raise TranscriptionError(
            f"Оба провайдера недоступны. Последняя ошибка: {e.message}"
        ) from e


# ===== PUBLIC ASYNC API =====


async def transcribe(
    file_path: Path,
    model: str | None = None,
) -> str:
    """
    Асинхронно транскрибирует аудиофайл.

    Автоматически нарезает файл на чанки если > 25MB.
    Использует Groq как основной провайдер, OmniRoute как fallback.

    Args:
        file_path: Путь к аудиофайлу (m4a, mp4, mp3, wav и др.).
        model: Модель Whisper (по умолчанию из конфига).

    Returns:
        Полный текст транскрибации.

    Raises:
        ServiceDisabledError: Если модуль отключён.
        TranscriptionError: При ошибке транскрибации.
    """
    _check_enabled()

    if not file_path.exists():
        raise TranscriptionError(f"Файл не найден: {file_path}")

    if model is None:
        model = WHISPER_MODEL

    file_size_mb = file_path.stat().st_size / (1024 * 1024)
    logger.info(
        f"Начало транскрибации: {file_path.name} "
        f"({file_size_mb:.1f} MB, model={model})"
    )

    # Нарезка если нужно
    chunks = await split_audio(file_path)

    # Транскрибируем чанки
    texts: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        if len(chunks) > 1:
            logger.info(f"Транскрибация чанка {i}/{len(chunks)}: {chunk.name}")
        text = await _transcribe_with_fallback(chunk, model)
        texts.append(text)

    # Очистка чанков (если были)
    if len(chunks) > 1:
        cleanup_chunks(chunks, original=file_path)

    full_text = "\n".join(texts)
    logger.info(
        f"Транскрибация завершена: {len(full_text)} символов, "
        f"{len(chunks)} чанк(ов)"
    )
    return full_text


def get_available_models() -> dict[str, str]:
    """
    Возвращает словарь доступных моделей для выбора в боте.

    Returns:
        dict[model_id, описание]
    """
    return AVAILABLE_MODELS.copy()

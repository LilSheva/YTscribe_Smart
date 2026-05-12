"""
run_tests.py — Диагностика локальной среды YTS_bot.

Запуск: python run_tests.py

Проверяет:
  1. Наличие .env и критичных переменных (BOT_TOKEN, API ключи).
  2. Наличие ffmpeg в системном PATH.
  3. Статус Feature Toggles.
  4. Доступность временной директории.
"""

import shutil
import sys
import logging
from pathlib import Path

# Добавляем корень проекта в path (на случай запуска из подпапки)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config import (
    BASE_DIR,
    BOT_TOKEN,
    GROQ_API_KEY,
    OMNIROUTE_API_KEY,
    GDRIVE_CREDENTIALS_PATH,
    TEMP_DIR,
    LOG_LEVEL,
    features,
)
from core.logger import setup_logging

# Инициализация логгера
setup_logging(level=LOG_LEVEL)
logger = logging.getLogger(__name__)


def check_env_file() -> bool:
    """Проверяет наличие .env файла."""
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        logger.info(f"✅ .env найден: {env_path}")
        return True
    else:
        logger.error(f"❌ .env НЕ НАЙДЕН! Скопируйте: cp .env.example .env")
        return False


def check_tokens() -> bool:
    """Проверяет наличие критичных API-токенов."""
    all_ok = True

    # BOT_TOKEN — обязательный
    if BOT_TOKEN and not BOT_TOKEN.startswith("123456"):
        logger.info("✅ BOT_TOKEN задан")
    else:
        logger.error("❌ BOT_TOKEN отсутствует или содержит placeholder!")
        all_ok = False

    # GROQ_API_KEY — нужен если включена транскрибация
    if features.transcript:
        if GROQ_API_KEY and GROQ_API_KEY.startswith("gsk_"):
            logger.info("✅ GROQ_API_KEY задан")
        elif GROQ_API_KEY and not GROQ_API_KEY.startswith("gsk_x"):
            logger.info("✅ GROQ_API_KEY задан (нестандартный формат)")
        else:
            logger.warning("⚠️  GROQ_API_KEY отсутствует (ENABLE_TRANSCRIPT=True)")
            all_ok = False
    else:
        logger.info("⏭  GROQ_API_KEY — пропуск (транскрибация отключена)")

    # OMNIROUTE_API_KEY — нужен если включен LLM
    if features.llm:
        if OMNIROUTE_API_KEY and not OMNIROUTE_API_KEY.startswith("sk-x"):
            logger.info("✅ OMNIROUTE_API_KEY задан")
        else:
            logger.warning("⚠️  OMNIROUTE_API_KEY отсутствует (ENABLE_LLM=True)")
            all_ok = False
    else:
        logger.info("⏭  OMNIROUTE_API_KEY — пропуск (LLM отключен)")

    return all_ok


def check_ffmpeg() -> bool:
    """Проверяет наличие ffmpeg в PATH."""
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        logger.info(f"✅ ffmpeg найден: {ffmpeg_path}")
        return True
    else:
        logger.error("❌ ffmpeg НЕ НАЙДЕН в PATH! Установите: https://ffmpeg.org/download.html")
        return False


def check_temp_dir() -> bool:
    """Проверяет/создает временную директорию."""
    try:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        # Проверка прав записи
        test_file = TEMP_DIR / ".write_test"
        test_file.write_text("ok")
        test_file.unlink()
        logger.info(f"✅ Временная директория доступна: {TEMP_DIR}")
        return True
    except OSError as e:
        logger.error(f"❌ Ошибка доступа к {TEMP_DIR}: {e}")
        return False


def check_gdrive_credentials() -> bool:
    """Проверяет наличие файла credentials для Google Drive."""
    if not features.gdrive:
        logger.info("⏭  Google Drive — пропуск (ENABLE_GDRIVE=False)")
        return True

    creds_path = BASE_DIR / GDRIVE_CREDENTIALS_PATH
    if creds_path.exists():
        logger.info(f"✅ GDrive credentials найдены: {creds_path}")
        return True
    else:
        logger.warning(f"⚠️  GDrive credentials НЕ найдены: {creds_path}")
        return False


def main() -> None:
    """Главная функция диагностики."""
    logger.info("=" * 50)
    logger.info("🔍 YTS_bot — Диагностика окружения")
    logger.info("=" * 50)

    results: list[tuple[str, bool]] = []

    # --- Проверки ---
    results.append((".env файл", check_env_file()))
    results.append(("API токены", check_tokens()))
    results.append(("ffmpeg", check_ffmpeg()))
    results.append(("Temp директория", check_temp_dir()))
    results.append(("GDrive credentials", check_gdrive_credentials()))

    # --- Feature Toggles ---
    logger.info("")
    logger.info(features.summary())

    # --- Итог ---
    logger.info("")
    logger.info("=" * 50)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    logger.info(f"Результат: {passed}/{total} проверок пройдено")

    if passed == total:
        logger.info("🎉 Всё готово к запуску бота!")
    else:
        logger.warning("⚠️  Есть проблемы. Исправьте ошибки выше и перезапустите.")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()

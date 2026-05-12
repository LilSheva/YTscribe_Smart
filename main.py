"""
main.py — Точка входа YTS_bot.

Инициализация бота, подключение роутеров, startup cleanup, запуск polling.
"""

import asyncio
import logging
import time
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from core.config import BOT_TOKEN, TEMP_DIR, LOG_LEVEL
from core.logger import setup_logging
from bot.handlers.start import router as start_router
from bot.handlers.url_handler import router as url_router

# Инициализация логгера
setup_logging(level=LOG_LEVEL)
logger = logging.getLogger(__name__)

# Порог очистки temp/ (в секундах) — файлы старше 1 часа
CLEANUP_AGE_SECONDS: int = 3600


def startup_cleanup(temp_dir: Path, max_age_sec: int = CLEANUP_AGE_SECONDS) -> int:
    """
    Удаляет файлы из временной директории, если они старше max_age_sec.

    Args:
        temp_dir: Путь к временной папке.
        max_age_sec: Максимальный возраст файла в секундах.

    Returns:
        Количество удалённых файлов.
    """
    if not temp_dir.exists():
        temp_dir.mkdir(parents=True, exist_ok=True)
        return 0

    now = time.time()
    deleted = 0

    for file in temp_dir.iterdir():
        if file.is_file():
            age = now - file.stat().st_mtime
            if age > max_age_sec:
                try:
                    file.unlink()
                    deleted += 1
                    logger.debug(f"Удалён старый файл: {file.name} (возраст: {age:.0f}с)")
                except OSError as e:
                    logger.warning(f"Не удалось удалить {file.name}: {e}")

    return deleted


async def on_startup(bot: Bot) -> None:
    """Действия при запуске бота."""
    # Очистка temp/
    deleted = startup_cleanup(TEMP_DIR)
    if deleted:
        logger.info(f"🧹 Startup cleanup: удалено {deleted} старых файлов из temp/")
    else:
        logger.info("🧹 Startup cleanup: temp/ чист")

    # Информация о боте
    bot_info = await bot.get_me()
    logger.info(f"🤖 Бот запущен: @{bot_info.username} (id={bot_info.id})")


async def on_shutdown(bot: Bot) -> None:
    """Действия при остановке бота."""
    logger.info("🛑 Бот останавливается...")
    await bot.session.close()


async def main() -> None:
    """Главная функция: создание бота, регистрация роутеров, запуск."""
    if not BOT_TOKEN or BOT_TOKEN.startswith("123456"):
        logger.error("❌ BOT_TOKEN не задан! Заполните .env файл.")
        return

    # Создание бота с дефолтным parse_mode
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )

    # Dispatcher
    dp = Dispatcher()

    # Регистрация роутеров
    dp.include_router(start_router)
    dp.include_router(url_router)

    # Lifecycle hooks
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Запуск polling
    logger.info("⏳ Запуск polling...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())

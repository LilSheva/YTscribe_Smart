"""
main.py — Точка входа YTS_bot.

Инициализация бота, подключение роутеров, startup cleanup, запуск polling.
"""

import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import TelegramObject, Update, ErrorEvent

from core.config import BOT_TOKEN, TEMP_DIR, LOG_LEVEL, ALLOWED_USER_IDS, BASE_DIR, ENABLE_GDRIVE, GDRIVE_SYNC_ON_START, GDRIVE_SYNC_AUTO_REPAIR
from services.db import init_db
from services.cookie_manager import maybe_refresh_cookies
from core.logger import setup_logging, is_headless
from bot.handlers.start import router as start_router
from bot.handlers.url_handler import router as url_router
from bot.handlers.batch_handler import router as batch_router
from bot.handlers.gdrive_sync_handler import router as gdrive_sync_router
from bot.middleware.resilience import HandlerResilienceMiddleware

# Инициализация логгера (консоль + файл .run/yts_bot.log)
setup_logging(level=LOG_LEVEL, log_dir=BASE_DIR / ".run")
logger = logging.getLogger(__name__)


def _install_excepthook() -> None:
    """Необработанные исключения → yts_bot.log (важно для pythonw без консоли)."""

    def _hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logger.critical("Uncaught exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = _hook


_install_excepthook()

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


async def _startup_gdrive_sync() -> None:
    """Фоновый аудит (и опционально repair) GDrive при старте бота."""
    from services import gdrive_sync

    try:
        report = await asyncio.to_thread(gdrive_sync.audit)
        if report.issue_count:
            logger.info(
                "GDrive startup audit: %s issue(s) on %s video(s)",
                report.issue_count,
                len(report.affected_video_ids),
            )
            if GDRIVE_SYNC_AUTO_REPAIR:
                batch = await asyncio.to_thread(gdrive_sync.repair_all, dry_run=False)
                logger.info(
                    "GDrive startup repair: ok=%s failed=%s",
                    batch.repaired,
                    batch.failed,
                )
        else:
            logger.info("GDrive startup audit: all OK (%s records)", report.scanned)
    except Exception as e:
        logger.warning("GDrive startup sync failed: %s", e)


async def on_startup(bot: Bot) -> None:
    """Действия при запуске бота."""
    init_db()
    # Очистка temp/
    deleted = startup_cleanup(TEMP_DIR)
    if deleted:
        logger.info(f"Startup cleanup: удалено {deleted} старых файлов из temp/")
    else:
        logger.info("Startup cleanup: temp/ чист")

    # Фоновое обновление cookies.txt из браузера
    asyncio.create_task(maybe_refresh_cookies())

    if ENABLE_GDRIVE and GDRIVE_SYNC_ON_START:
        asyncio.create_task(_startup_gdrive_sync())

    # Информация о боте
    bot_info = await bot.get_me()
    logger.info(f"Бот запущен: @{bot_info.username} (id={bot_info.id})")


async def on_shutdown(bot: Bot) -> None:
    """Действия при остановке бота."""
    logger.info("Bot stopping...")
    await bot.session.close()


async def main() -> None:
    """Главная функция: создание бота, регистрация роутеров, запуск."""
    if not BOT_TOKEN or BOT_TOKEN.startswith("123456"):
        logger.error("BOT_TOKEN not set! Fill in .env file.")
        return

    # Создание бота с дефолтным parse_mode
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Dispatcher
    dp = Dispatcher(storage=MemoryStorage())

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        """Запасной обработчик, если исключение прошло мимо middleware."""
        logger.exception("Handler error (errors router): %s", event.exception)
        if event.update and event.bot:
            from bot.middleware.error_notify import notify_update_failure

            await notify_update_failure(event.bot, event.update, event.exception)
        return True

    dp.update.outer_middleware(HandlerResilienceMiddleware())
    if ALLOWED_USER_IDS:
        class AllowlistMiddleware(BaseMiddleware):
            async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
                update: Update = data.get("event_update") or event
                user_id = None
                if hasattr(update, "message") and update.message:
                    user_id = update.message.from_user.id if update.message.from_user else None
                elif hasattr(update, "callback_query") and update.callback_query:
                    user_id = update.callback_query.from_user.id if update.callback_query.from_user else None
                if user_id not in ALLOWED_USER_IDS:
                    logger.warning(f"Blocked unauthorized user_id={user_id}")
                    return
                return await handler(event, data)

        dp.update.outer_middleware(AllowlistMiddleware())
        logger.info(f"Whitelist active: {ALLOWED_USER_IDS}")

    # Регистрация роутеров
    dp.include_router(start_router)
    dp.include_router(gdrive_sync_router)
    dp.include_router(batch_router)
    dp.include_router(url_router)

    # Lifecycle hooks
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Запуск polling
    logger.info("Starting polling...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.exception("Fatal startup error")
        if not is_headless():
            try:
                input("Press Enter to exit...")
            except EOFError:
                pass

"""
bot/handlers/start.py — Обработчики команд /start и /help.
"""

import logging

from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

from core.config import features

router = Router(name="start")
logger = logging.getLogger(__name__)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Приветственное сообщение при /start."""
    user_name = message.from_user.first_name if message.from_user else "друг"
    text = (
        f"👋 Привет, {user_name}!\n\n"
        "Я **YTS_bot** — твой помощник для работы с YouTube-контентом.\n\n"
        "Отправь мне ссылку на видео, и я предложу:\n"
    )

    # Динамический список возможностей на основе Feature Toggles
    capabilities: list[str] = []
    if features.downloader:
        capabilities.append("🎵 Скачать аудио/видео")
    if features.transcript:
        capabilities.append("📝 Транскрибировать речь в текст")
    if features.llm:
        capabilities.append("🧠 Сделать умное саммари (AI)")
    if features.db:
        capabilities.append("🗃 Сохранить в базу знаний")

    if capabilities:
        text += "\n".join(f"• {c}" for c in capabilities)
    else:
        text += "⚠️ Все модули отключены. Проверьте конфигурацию."

    text += "\n\n💡 Используй /help для списка команд."

    await message.answer(text, parse_mode="Markdown")
    logger.info(f"Пользователь {message.from_user.id} запустил бота")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Список доступных команд с учётом Feature Toggles."""
    lines: list[str] = [
        "📖 **Доступные команды:**\n",
        "/start — Запустить бота",
        "/help — Показать эту справку",
    ]

    if features.downloader:
        lines.append("/download — Скачать медиа по ссылке")
    if features.transcript:
        lines.append("/transcribe — Транскрибировать видео")
    if features.llm:
        lines.append("/analyze — AI-анализ контента")
    if features.db:
        lines.append("/search — Поиск по базе знаний")

    lines.append("\n💬 Или просто отправь ссылку на YouTube!")

    await message.answer("\n".join(lines), parse_mode="Markdown")
    logger.info(f"Пользователь {message.from_user.id} запросил /help")

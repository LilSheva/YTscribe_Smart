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
        "Я <b>YTS_bot</b> — твой помощник для работы с YouTube-контентом.\n\n"
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

    await message.answer(text, parse_mode="HTML")
    logger.info(f"Пользователь {message.from_user.id} запустил бота")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Список доступных команд с учётом Feature Toggles."""
    lines: list[str] = [
        "<b>📖 Доступные команды:</b>\n",
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

    await message.answer("\n".join(lines), parse_mode="HTML")
    logger.info(f"Пользователь {message.from_user.id} запросил /help")


@router.message(Command("search"))
async def cmd_search(message: Message) -> None:
    """Поиск по внешней базе знаний (KB API)."""
    from core.config import ENABLE_KB, KB_API_URL

    if not ENABLE_KB:
        await message.answer(
            "🗃 База знаний отключена (`ENABLE_KB=False`).",
            parse_mode="Markdown",
        )
        return

    if not KB_API_URL:
        await message.answer(
            "⚠️ База знаний не подключена.\n"
            "Укажите `KB_API_URL` в `.env` для интеграции с внешним сервисом.",
            parse_mode="Markdown",
        )
        return

    # Извлекаем запрос из сообщения
    query = (message.text or "").replace("/search", "").strip()
    if not query:
        await message.answer(
            "🔍 Использование: `/search ваш запрос`\n\n"
            "Например: `/search как работает attention mechanism`",
            parse_mode="Markdown",
        )
        return

    # Обращение к внешнему API
    import httpx

    await message.answer("🔍 Ищу в базе знаний...")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                KB_API_URL,
                json={"query": query, "top_k": 5},
            )

        if response.status_code == 200:
            data = response.json()
            results = data.get("results", [])
            if results:
                lines = ["🗃 **Результаты поиска:**\n"]
                for i, r in enumerate(results, 1):
                    text_snippet = r.get("text", "")[:200]
                    source = r.get("source", "—")
                    lines.append(f"**{i}.** _{source}_\n{text_snippet}...\n")
                await message.answer("\n".join(lines), parse_mode="Markdown")
            else:
                await message.answer("🤷 Ничего не найдено по вашему запросу.")
        else:
            await message.answer(f"❌ KB API вернул ошибку: {response.status_code}")

    except httpx.TimeoutException:
        await message.answer("⏱ Timeout: база знаний не ответила вовремя.")
    except Exception as e:
        logger.error(f"KB search error: {e}")
        await message.answer("❌ Ошибка при обращении к базе знаний.")

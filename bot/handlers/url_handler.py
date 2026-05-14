"""
bot/handlers/url_handler.py — Обработка YouTube-ссылок и callback-действий.

Поток:
  1. Пользователь отправляет ссылку → get_info() → карточка + клавиатура.
  2. Нажатие кнопки → скачивание → GDrive upload → ссылка юзеру.
  3. Если файл <= 50MB → кнопка "Получить в чат".
"""

import re
import uuid
import logging
from pathlib import Path

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile

from core.config import features, ENABLE_GDRIVE
from core.models import MediaTask
from core.exceptions import ServiceDisabledError, DownloadError, TranscriptionError, LLMError, YTSBotError
from services import downloader
from services import gdrive
from services import transcriber
from services import llm_router
from bot.keyboards.main_menu import get_media_keyboard, get_post_download_keyboard

router = Router(name="url_handler")
logger = logging.getLogger(__name__)

# Хранилище задач в памяти (task_id -> данные)
# В продакшене можно заменить на Redis/FSM Storage
_tasks: dict[str, dict] = {}

# Лимит Telegram Bot API для отправки файлов (50 MB)
TELEGRAM_FILE_LIMIT_MB: float = 50.0

# Regex для YouTube-ссылок
YOUTUBE_URL_PATTERN = re.compile(
    r"(https?://)?(www\.)?"
    r"(youtube\.com/(watch\?v=|shorts/|live/)|youtu\.be/)"
    r"[\w\-]{11}"
)


def _extract_url(text: str) -> str | None:
    """Извлекает YouTube URL из текста сообщения."""
    match = YOUTUBE_URL_PATTERN.search(text)
    if match:
        return match.group(0)
    return None


def _generate_task_id() -> str:
    """Генерирует короткий уникальный ID для callback_data."""
    return uuid.uuid4().hex[:8]


@router.message(F.text.regexp(YOUTUBE_URL_PATTERN))
async def handle_youtube_url(message: Message) -> None:
    """Обработка входящей YouTube-ссылки: парсинг метаданных + клавиатура."""
    url = _extract_url(message.text or "")
    if not url:
        return

    # Убедимся что URL полный
    if not url.startswith("http"):
        url = "https://" + url

    logger.info(f"Получена ссылка от {message.from_user.id}: {url}")

    # Статус-сообщение
    status_msg = await message.answer("⏳ Получаю информацию о видео...")

    try:
        task = await downloader.get_info(url)
    except ServiceDisabledError as e:
        await status_msg.edit_text(f"⚠️ {e.message}", parse_mode=None)
        return
    except DownloadError as e:
        await status_msg.edit_text(f"❌ {e.message}", parse_mode=None)
        return
    except Exception as e:
        logger.error(f"Unexpected error in get_info: {e}")
        await status_msg.edit_text("❌ Не удалось получить информацию о видео.")
        return

    # Сохраняем задачу
    task_id = _generate_task_id()
    _tasks[task_id] = {
        "url": url,
        "task": task,
        "user_id": message.from_user.id,
    }

    # Карточка с метаданными
    card_text = (
        f"🎬 **{task.title}**\n"
        f"📺 {task.channel}\n"
        f"⏱ {task.duration_formatted}\n\n"
        f"Выберите действие:"
    )

    keyboard = get_media_keyboard(task_id)
    await status_msg.edit_text(card_text, reply_markup=keyboard, parse_mode="Markdown")


@router.callback_query(F.data.startswith("dl_m4a:"))
async def cb_download_m4a(callback: CallbackQuery) -> None:
    """Callback: скачивание M4A → GDrive → ссылка."""
    await _handle_download(callback, format_type="m4a")


@router.callback_query(F.data.startswith("dl_mp4:"))
async def cb_download_mp4(callback: CallbackQuery) -> None:
    """Callback: скачивание MP4 → GDrive → ссылка."""
    await _handle_download(callback, format_type="mp4")


@router.callback_query(F.data.startswith("send_chat:"))
async def cb_send_to_chat(callback: CallbackQuery) -> None:
    """Callback: отправка файла в чат (если <= 50MB)."""
    task_id = callback.data.split(":")[1]
    task_data = _tasks.get(task_id)

    if not task_data or "file_path" not in task_data:
        await callback.answer("❌ Файл не найден или уже удалён.", show_alert=True)
        return

    file_path: Path = task_data["file_path"]
    if not file_path.exists():
        await callback.answer("❌ Файл уже удалён с диска.", show_alert=True)
        return

    size_mb = file_path.stat().st_size / (1024 * 1024)
    if size_mb > TELEGRAM_FILE_LIMIT_MB:
        await callback.answer(
            f"⚠️ Файл слишком большой ({size_mb:.1f} MB > {TELEGRAM_FILE_LIMIT_MB} MB)",
            show_alert=True,
        )
        return

    await callback.answer("📤 Отправляю файл...")

    try:
        document = FSInputFile(file_path)
        await callback.message.answer_document(
            document=document,
            caption=f"📁 {file_path.name}",
        )
        logger.info(f"Файл отправлен в чат: {file_path.name} ({size_mb:.1f} MB)")
    except Exception as e:
        logger.error(f"Ошибка отправки файла в чат: {e}")
        await callback.message.answer(f"❌ Не удалось отправить файл: {e}")


async def _handle_download(callback: CallbackQuery, format_type: str) -> None:
    """
    Общая логика скачивания: download → GDrive upload → ответ юзеру.

    Args:
        callback: Callback query от кнопки.
        format_type: Формат файла ("m4a" или "mp4").
    """
    task_id = callback.data.split(":")[1]
    task_data = _tasks.get(task_id)

    if not task_data:
        await callback.answer("❌ Задача не найдена. Отправьте ссылку заново.", show_alert=True)
        return

    url = task_data["url"]
    task: MediaTask = task_data["task"]

    await callback.answer()

    # Обновляем сообщение — статус загрузки
    await callback.message.edit_text(
        f"⬇️ Скачиваю **{task.title}** ({format_type.upper()})...",
        parse_mode="Markdown",
    )

    # --- Скачивание ---
    try:
        result_task = await downloader.download_media(url, format_type)
    except (ServiceDisabledError, DownloadError) as e:
        await callback.message.edit_text(f"❌ {e.message}", parse_mode=None)
        return
    except Exception as e:
        logger.error(f"Download error: {e}")
        await callback.message.edit_text("❌ Ошибка при скачивании.")
        return

    file_path = result_task.temp_file_path
    if not file_path or not file_path.exists():
        await callback.message.edit_text("❌ Файл не найден после загрузки.")
        return

    size_mb = file_path.stat().st_size / (1024 * 1024)
    task_data["file_path"] = file_path

    # --- Google Drive upload ---
    gdrive_url: str | None = None
    if ENABLE_GDRIVE:
        await callback.message.edit_text(
            f"☁️ Загружаю на Google Drive ({size_mb:.1f} MB)...",
            parse_mode="Markdown",
        )
        try:
            gdrive_result = await gdrive.upload(file_path)
            gdrive_url = gdrive_result.public_url
        except (ServiceDisabledError, YTSBotError) as e:
            logger.warning(f"GDrive upload failed: {e.message}")
            gdrive_url = None
        except Exception as e:
            logger.error(f"GDrive unexpected error: {e}")
            gdrive_url = None

    # --- Формируем ответ ---
    can_send = size_mb <= TELEGRAM_FILE_LIMIT_MB

    lines: list[str] = [
        f"✅ **{task.title}**",
        f"📦 Формат: {format_type.upper()} | Размер: {size_mb:.1f} MB",
    ]

    if gdrive_url:
        lines.append(f"\n☁️ **Google Drive:** [Открыть файл]({gdrive_url})")
    elif ENABLE_GDRIVE:
        lines.append("\n⚠️ Загрузка на GDrive не удалась.")

    if can_send:
        lines.append("\n📥 Файл можно получить в чат (кнопка ниже).")
    else:
        lines.append(f"\n⚠️ Файл > {TELEGRAM_FILE_LIMIT_MB:.0f} MB — отправка в чат невозможна.")

    keyboard = get_post_download_keyboard(task_id, can_send_to_chat=can_send)

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=keyboard,
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )

    logger.info(
        f"Download complete: {file_path.name} ({size_mb:.1f} MB), "
        f"GDrive: {'OK' if gdrive_url else 'SKIP'}, "
        f"Chat send: {'available' if can_send else 'too large'}"
    )


@router.callback_query(F.data.startswith("transcript:"))
async def cb_transcribe(callback: CallbackQuery) -> None:
    """Callback: скачивание аудио → транскрибация → текст в чат."""
    task_id = callback.data.split(":")[1]
    task_data = _tasks.get(task_id)

    if not task_data:
        await callback.answer("❌ Задача не найдена. Отправьте ссылку заново.", show_alert=True)
        return

    url = task_data["url"]
    task: MediaTask = task_data["task"]

    await callback.answer()

    # Статус: скачиваю аудио
    await callback.message.edit_text(
        f"⬇️ Скачиваю аудио **{task.title}**...",
        parse_mode="Markdown",
    )

    # --- Скачивание M4A ---
    try:
        result_task = await downloader.download_media(url, "m4a")
    except (ServiceDisabledError, DownloadError) as e:
        await callback.message.edit_text(f"❌ {e.message}", parse_mode=None)
        return
    except Exception as e:
        logger.error(f"Download for transcript error: {e}")
        await callback.message.edit_text("❌ Ошибка при скачивании аудио.")
        return

    file_path = result_task.temp_file_path
    if not file_path or not file_path.exists():
        await callback.message.edit_text("❌ Аудиофайл не найден после загрузки.")
        return

    task_data["file_path"] = file_path

    # Статус: транскрибирую
    size_mb = file_path.stat().st_size / (1024 * 1024)
    await callback.message.edit_text(
        f"📝 Транскрибирую **{task.title}** ({size_mb:.1f} MB)...\n"
        f"Это может занять 30–120 секунд.",
        parse_mode="Markdown",
    )

    # --- Транскрибация ---
    try:
        text = await transcriber.transcribe(file_path)
    except (ServiceDisabledError, TranscriptionError) as e:
        await callback.message.edit_text(f"❌ {e.message}", parse_mode=None)
        return
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        await callback.message.edit_text("❌ Ошибка транскрибации.")
        return

    # Сохраняем транскрипт в задачу
    task_data["transcript"] = text

    # --- Автосохранение .md на GDrive ---
    gdrive_md_url: str | None = None
    if ENABLE_GDRIVE:
        try:
            from utils.md_generator import generate_transcript_md
            md_path = generate_transcript_md(task, text)
            gdrive_result = await gdrive.upload_transcript(md_path)
            gdrive_md_url = gdrive_result.public_url
            # Удаляем временный .md
            md_path.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Автосохранение транскрипта на GDrive: {e}")

    # --- Результат ---
    status_lines = [
        f"✅ Транскрибация завершена: **{task.title}**",
        f"📄 {len(text)} символов",
    ]
    if gdrive_md_url:
        status_lines.append(f"☁️ [Транскрипт на GDrive]({gdrive_md_url})")
    else:
        status_lines.append("⚠️ Не удалось загрузить на GDrive")

    await callback.message.edit_text(
        "\n".join(status_lines),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )

    logger.info(f"Transcript complete: {task.title} ({len(text)} chars), GDrive: {'OK' if gdrive_md_url else 'SKIP'}")


@router.callback_query(F.data.startswith("llm_analyze:"))
async def cb_llm_analyze(callback: CallbackQuery) -> None:
    """Callback: скачивание → транскрибация → LLM анализ → результат в чат."""
    task_id = callback.data.split(":")[1]
    task_data = _tasks.get(task_id)

    if not task_data:
        await callback.answer("❌ Задача не найдена. Отправьте ссылку заново.", show_alert=True)
        return

    url = task_data["url"]
    task: MediaTask = task_data["task"]

    await callback.answer()

    # Если транскрипт уже есть — используем его
    text = task_data.get("transcript")

    if not text:
        # Скачиваем аудио
        await callback.message.edit_text(
            f"⬇️ Скачиваю аудио **{task.title}**...",
            parse_mode="Markdown",
        )

        try:
            result_task = await downloader.download_media(url, "m4a")
        except (ServiceDisabledError, DownloadError) as e:
            await callback.message.edit_text(f"❌ {e.message}", parse_mode=None)
            return
        except Exception as e:
            logger.error(f"Download for LLM error: {e}")
            await callback.message.edit_text("❌ Ошибка при скачивании аудио.")
            return

        file_path = result_task.temp_file_path
        if not file_path or not file_path.exists():
            await callback.message.edit_text("❌ Аудиофайл не найден после загрузки.")
            return

        task_data["file_path"] = file_path

        # Транскрибируем
        size_mb = file_path.stat().st_size / (1024 * 1024)
        await callback.message.edit_text(
            f"📝 Транскрибирую **{task.title}** ({size_mb:.1f} MB)...",
            parse_mode="Markdown",
        )

        try:
            text = await transcriber.transcribe(file_path)
            task_data["transcript"] = text
        except (ServiceDisabledError, TranscriptionError) as e:
            await callback.message.edit_text(f"❌ {e.message}", parse_mode=None)
            return
        except Exception as e:
            logger.error(f"Transcription for LLM error: {e}")
            await callback.message.edit_text("❌ Ошибка транскрибации.")
            return

    # --- LLM анализ ---
    await callback.message.edit_text(
        f"🧠 AI анализирует **{task.title}**...\n"
        f"Модель обрабатывает {len(text)} символов.",
        parse_mode="Markdown",
    )

    try:
        result = await llm_router.analyze(text)
    except (ServiceDisabledError, LLMError) as e:
        await callback.message.edit_text(f"❌ {e.message}", parse_mode=None)
        return
    except Exception as e:
        logger.error(f"LLM analyze error: {e}")
        await callback.message.edit_text("❌ Ошибка при AI-анализе.")
        return

    # --- Отправка результата ---
    header = f"🧠 **AI Саммари: {task.title}**\n\n"
    parts = llm_router.split_for_telegram(header + result)

    for part in parts:
        await callback.message.answer(part, parse_mode="Markdown")

    # --- Автосохранение саммари на GDrive ---
    gdrive_md_url: str | None = None
    if ENABLE_GDRIVE:
        try:
            from utils.md_generator import generate_transcript_md
            # Сохраняем саммари (транскрипт + саммари вместе)
            combined = f"{text}\n\n---\n\n## AI Саммари\n\n{result}"
            md_path = generate_transcript_md(task, combined)
            gdrive_result = await gdrive.upload_transcript(md_path)
            gdrive_md_url = gdrive_result.public_url
            md_path.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Автосохранение саммари на GDrive: {e}")

    # Обновляем исходное сообщение
    status_lines = [
        f"✅ AI-анализ завершён: **{task.title}**",
        f"📝 Транскрипт: {len(text)} символов",
        f"🧠 Ответ: {len(result)} символов",
    ]
    if gdrive_md_url:
        status_lines.append(f"☁️ [Документ на GDrive]({gdrive_md_url})")

    await callback.message.edit_text(
        "\n".join(status_lines),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )

    logger.info(f"LLM analyze complete: {task.title} ({len(result)} chars), GDrive: {'OK' if gdrive_md_url else 'SKIP'}")


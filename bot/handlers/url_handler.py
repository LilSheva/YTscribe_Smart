"""
bot/handlers/url_handler.py — Обработка YouTube-ссылок и callback-действий.

Поток:
  1. Пользователь отправляет ссылку → get_info() → карточка + клавиатура.
  2. Нажатие кнопки → скачивание → GDrive upload → ссылка юзеру.
  3. Если файл <= 50MB → кнопка "Получить в чат".
"""

import re
import uuid
import asyncio
import time
import logging
from pathlib import Path

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from core.config import features, ENABLE_GDRIVE
from core.models import MediaTask
from core.exceptions import ServiceDisabledError, DownloadError, TranscriptionError, LLMError, YTSBotError
from services import db
from services import downloader
from services import gdrive
from services import transcriber
from services import llm_router
from bot.keyboards.main_menu import get_media_keyboard, get_post_download_keyboard, get_analysis_variants_keyboard, get_analysis_menu_keyboard

class VariantRefreshState(StatesGroup):
    waiting_comment = State()


router = Router(name="url_handler")
logger = logging.getLogger(__name__)

_tasks: dict[str, dict] = {}

TELEGRAM_FILE_LIMIT_MB: float = 50.0

YOUTUBE_URL_PATTERN = re.compile(
    r"(https?://)?(www\.)?"
    r"(youtube\.com/(watch\?v=|shorts/|live/)|youtu\.be/)"
    r"[\w\-]{11}"
)

# Groq обрабатывает ~216x realtime
_GROQ_REALTIME_FACTOR = 216


def _progress_bar(pct: float, width: int = 10) -> str:
    filled = round(pct * width)
    return "█" * filled + "░" * (width - filled)


async def _ticker(
    msg: CallbackQuery | Message,
    prefix: str,
    *,
    total_sec: float | None = None,
    done_event: asyncio.Event,
    interval: float = 3.0,
) -> None:
    """Обновляет сообщение каждые interval секунд пока не установлен done_event."""
    start = time.monotonic()
    message = msg.message if isinstance(msg, CallbackQuery) else msg
    while not done_event.is_set():
        await asyncio.sleep(interval)
        if done_event.is_set():
            break
        elapsed = time.monotonic() - start
        if total_sec:
            pct = min(elapsed / total_sec, 0.95)
            remaining = max(total_sec - elapsed, 1)
            bar = _progress_bar(pct)
            text = f"{prefix}\n[{bar}] {pct*100:.0f}% • ~{remaining:.0f}с осталось"
        else:
            text = f"{prefix}\n⏱ {elapsed:.0f}с..."
        try:
            await message.edit_text(text)
        except Exception:
            break


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

    # Проверяем дубликат в БД
    db.upsert_video(task, message.from_user.id)
    state = db.get_state(task.video_id)
    cached_text = db.get_transcript_text(task.video_id) if state and state.has_transcript else None

    # Сохраняем задачу
    task_id = _generate_task_id()
    _tasks[task_id] = {
        "url": url,
        "task": task,
        "user_id": message.from_user.id,
    }
    if cached_text:
        _tasks[task_id]["transcript"] = cached_text

    # Карточка с метаданными
    cached_note = "\n♻️ _Транскрипт уже есть в истории_" if cached_text else ""
    card_text = (
        f"🎬 **{task.title}**\n"
        f"📺 {task.channel}\n"
        f"⏱ {task.duration_formatted}"
        f"{cached_note}\n\n"
        f"Выберите действие:"
    )

    keyboard = get_media_keyboard(task_id, has_transcript=bool(cached_text))
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
    task_id = callback.data.split(":")[1]
    task_data = _tasks.get(task_id)

    if not task_data:
        await callback.answer("❌ Задача не найдена. Отправьте ссылку заново.", show_alert=True)
        return

    url = task_data["url"]
    task: MediaTask = task_data["task"]

    await callback.answer()

    prefix = f"⬇️ Скачиваю **{task.title}** ({format_type.upper()})"
    await callback.message.edit_text(prefix + "...", parse_mode="Markdown")

    # Прогресс скачивания — реальный % из yt-dlp progress_hooks
    done_event = asyncio.Event()
    last_pct: list[float] = [0.0]

    def on_progress(pct: float) -> None:
        last_pct[0] = pct

    async def _dl_ticker() -> None:
        while not done_event.is_set():
            await asyncio.sleep(2)
            if done_event.is_set():
                break
            pct = last_pct[0]
            bar = _progress_bar(pct)
            try:
                await callback.message.edit_text(
                    f"{prefix}\n[{bar}] {pct*100:.0f}%",
                    parse_mode="Markdown",
                )
            except Exception:
                break

    ticker_task = asyncio.create_task(_dl_ticker())
    try:
        result_task = await downloader.download_media(url, format_type, on_progress=on_progress)
    except (ServiceDisabledError, DownloadError) as e:
        done_event.set()
        ticker_task.cancel()
        await callback.message.edit_text(f"❌ {e.message}", parse_mode=None)
        return
    except Exception as e:
        done_event.set()
        ticker_task.cancel()
        logger.error(f"Download error: {e}")
        await callback.message.edit_text("❌ Ошибка при скачивании.")
        return
    finally:
        done_event.set()
        ticker_task.cancel()

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
    task_id = callback.data.split(":")[1]
    task_data = _tasks.get(task_id)

    if not task_data:
        await callback.answer("❌ Задача не найдена. Отправьте ссылку заново.", show_alert=True)
        return

    url = task_data["url"]
    task: MediaTask = task_data["task"]
    await callback.answer()

    # Если транскрипт уже есть в кэше — пропускаем скачивание
    if task_data.get("transcript"):
        text = task_data["transcript"]
        entry = task_data.get("history_entry")
        gdrive_md_url = entry.gdrive_url if entry else None
        status_lines = [
            f"✅ Транскрибация завершена: **{task.title}**",
            f"📄 {len(text)} символов _(из кэша)_",
        ]
        if gdrive_md_url:
            status_lines.append(f"☁️ [Транскрипт на GDrive]({gdrive_md_url})")
        await callback.message.edit_text(
            "\n".join(status_lines), parse_mode="Markdown", disable_web_page_preview=True,
        )
        return

    # --- Скачивание с прогрессом ---
    prefix_dl = f"⬇️ Скачиваю аудио **{task.title}**"
    await callback.message.edit_text(prefix_dl + "...", parse_mode="Markdown")

    done_dl = asyncio.Event()
    last_pct: list[float] = [0.0]

    def on_progress(pct: float) -> None:
        last_pct[0] = pct

    async def _dl_ticker() -> None:
        while not done_dl.is_set():
            await asyncio.sleep(2)
            if done_dl.is_set():
                break
            bar = _progress_bar(last_pct[0])
            try:
                await callback.message.edit_text(
                    f"{prefix_dl}\n[{bar}] {last_pct[0]*100:.0f}%",
                    parse_mode="Markdown",
                )
            except Exception:
                break

    ticker = asyncio.create_task(_dl_ticker())
    try:
        result_task = await downloader.download_media(url, "m4a", on_progress=on_progress)
    except (ServiceDisabledError, DownloadError) as e:
        await callback.message.edit_text(f"❌ {e.message}", parse_mode=None)
        return
    except Exception as e:
        logger.error(f"Download for transcript error: {e}")
        await callback.message.edit_text("❌ Ошибка при скачивании аудио.")
        return
    finally:
        done_dl.set()
        ticker.cancel()

    file_path = result_task.temp_file_path
    if not file_path or not file_path.exists():
        await callback.message.edit_text("❌ Аудиофайл не найден после загрузки.")
        return
    task_data["file_path"] = file_path

    # --- Транскрибация с предиктивным прогрессом ---
    # Groq ~216x realtime: 20 мин аудио ≈ 5-6 сек
    transcribe_sec = max(task.duration_sec / _GROQ_REALTIME_FACTOR, 3.0)
    size_mb = file_path.stat().st_size / (1024 * 1024)
    prefix_tr = f"📝 Транскрибирую **{task.title}** ({size_mb:.1f} MB)"

    done_tr = asyncio.Event()
    ticker_tr = asyncio.create_task(
        _ticker(callback, prefix_tr, total_sec=transcribe_sec, done_event=done_tr)
    )
    await callback.message.edit_text(prefix_tr + "...", parse_mode="Markdown")

    try:
        text = await transcriber.transcribe(file_path)
    except (ServiceDisabledError, TranscriptionError) as e:
        await callback.message.edit_text(f"❌ {e.message}", parse_mode=None)
        return
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        await callback.message.edit_text("❌ Ошибка транскрибации.")
        return
    finally:
        done_tr.set()
        ticker_tr.cancel()

    task_data["transcript"] = text

    # --- Сохраняем транскрипт в БД и на диск ---
    md_path = db.save_transcript_file(task, text)

    # --- GDrive ---
    gdrive_md_url: str | None = None
    if ENABLE_GDRIVE:
        try:
            gdrive_result = await gdrive.upload_transcript(md_path)
            gdrive_md_url = gdrive_result.public_url
            db.set_gdrive_transcript(task.video_id, gdrive_md_url)
        except Exception as e:
            logger.warning(f"Автосохранение транскрипта на GDrive: {e}")

    status_lines = [
        f"✅ Транскрибация завершена: **{task.title}**",
        f"📄 {len(text)} символов",
    ]
    if gdrive_md_url:
        status_lines.append(f"☁️ [Транскрипт на GDrive]({gdrive_md_url})")
    else:
        status_lines.append("⚠️ Не удалось загрузить на GDrive")

    await callback.message.edit_text(
        "\n".join(status_lines), parse_mode="Markdown", disable_web_page_preview=True,
    )
    logger.info(f"Transcript complete: {task.title} ({len(text)} chars), GDrive: {'OK' if gdrive_md_url else 'SKIP'}")


@router.callback_query(F.data.startswith("llm_variants:"))
async def cb_llm_variants(callback: CallbackQuery) -> None:
    """Показывает варианты анализа после транскрипции."""
    task_id = callback.data.split(":")[1]
    task_data = _tasks.get(task_id)
    if not task_data:
        await callback.answer("❌ Задача не найдена. Отправьте ссылку заново.", show_alert=True)
        return

    url = task_data["url"]
    task: MediaTask = task_data["task"]
    await callback.answer()

    text = task_data.get("transcript")

    if not text:
        # Скачивание с прогрессом
        prefix_dl = f"⬇️ Скачиваю аудио **{task.title}**"
        await callback.message.edit_text(prefix_dl + "...", parse_mode="Markdown")

        done_dl = asyncio.Event()
        last_pct: list[float] = [0.0]

        def on_progress(pct: float) -> None:
            last_pct[0] = pct

        async def _dl_ticker() -> None:
            while not done_dl.is_set():
                await asyncio.sleep(2)
                if done_dl.is_set():
                    break
                bar = _progress_bar(last_pct[0])
                try:
                    await callback.message.edit_text(
                        f"{prefix_dl}\n[{bar}] {last_pct[0]*100:.0f}%",
                        parse_mode="Markdown",
                    )
                except Exception:
                    break

        ticker = asyncio.create_task(_dl_ticker())
        try:
            result_task = await downloader.download_media(url, "m4a", on_progress=on_progress)
        except (ServiceDisabledError, DownloadError) as e:
            await callback.message.edit_text(f"❌ {e.message}", parse_mode=None)
            return
        except Exception as e:
            logger.error(f"Download for variants error: {e}")
            await callback.message.edit_text("❌ Ошибка при скачивании аудио.")
            return
        finally:
            done_dl.set()
            ticker.cancel()

        file_path = result_task.temp_file_path
        if not file_path or not file_path.exists():
            await callback.message.edit_text("❌ Аудиофайл не найден после загрузки.")
            return
        task_data["file_path"] = file_path

        # Транскрибация с предиктивным прогрессом
        transcribe_sec = max(task.duration_sec / _GROQ_REALTIME_FACTOR, 3.0)
        size_mb = file_path.stat().st_size / (1024 * 1024)
        prefix_tr = f"📝 Транскрибирую **{task.title}** ({size_mb:.1f} MB)"
        await callback.message.edit_text(prefix_tr + "...", parse_mode="Markdown")

        done_tr = asyncio.Event()
        ticker_tr = asyncio.create_task(
            _ticker(callback, prefix_tr, total_sec=transcribe_sec, done_event=done_tr)
        )
        try:
            text = await transcriber.transcribe(file_path)
            task_data["transcript"] = text
        except (ServiceDisabledError, TranscriptionError) as e:
            await callback.message.edit_text(f"❌ {e.message}", parse_mode=None)
            return
        except Exception as e:
            logger.error(f"Transcription for variants error: {e}")
            await callback.message.edit_text("❌ Ошибка транскрибации.")
            return
        finally:
            done_tr.set()
            ticker_tr.cancel()

    # Проверяем сохранённые варианты
    cached_variants = db.get_variants(task.video_id)
    if cached_variants:
        variants = cached_variants
    else:
        await callback.message.edit_text("🤔 Анализирую содержание...", parse_mode=None)
        variants = await llm_router.get_analysis_variants(text)
        db.save_variants(task.video_id, variants)
    task_data["variants"] = variants

    past_results = [vars(r) for r in db.get_analysis_results(task.video_id)]
    keyboard = get_analysis_menu_keyboard(task_id, variants, past_results)
    await callback.message.edit_text(
        f"🎯 **{task.title}**\n\nВыберите тип анализа:",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


@router.callback_query(F.data.startswith("view_result:"))
async def cb_view_result(callback: CallbackQuery) -> None:
    result_id = int(callback.data.split(":")[1])
    r = db.get_analysis_result(result_id)
    if not r:
        await callback.answer("Ответ не найден.", show_alert=True)
        return
    await callback.answer()
    header = f"📄 **{r.label}**\n\n"
    for part in llm_router.split_for_telegram(header + r.result):
        await callback.message.answer(part, parse_mode="Markdown")


@router.callback_query(F.data.startswith("variants_refresh:"))
async def cb_variants_refresh(callback: CallbackQuery, state: FSMContext) -> None:
    task_id = callback.data.split(":")[1]
    await state.update_data(refresh_task_id=task_id, refresh_msg_id=callback.message.message_id)
    await callback.answer()
    await callback.message.answer("Введите комментарий для уточнения анализа (например: «сфокусируйся на технической части»):")
    await state.set_state(VariantRefreshState.waiting_comment)


@router.message(VariantRefreshState.waiting_comment)
async def cb_variants_refresh_comment(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    task_id = data.get("refresh_task_id")
    await state.clear()

    task_data = _tasks.get(task_id)
    if not task_data:
        await message.answer("Задача не найдена. Отправьте ссылку заново.")
        return

    task: MediaTask = task_data["task"]
    text: str = task_data.get("transcript", "")
    comment = message.text or ""

    status = await message.answer("🤔 Генерирую новые варианты...")
    new_variants = await llm_router.get_analysis_variants(text, extra_prompt=comment)

    # Дополняем существующие варианты новыми (не заменяем)
    existing = task_data.get("variants", [])
    max_idx = max((v["idx"] for v in existing), default=0)
    for i, v in enumerate(new_variants, 1):
        v["idx"] = max_idx + i
    all_variants = existing + new_variants
    task_data["variants"] = all_variants

    db.save_variants(task.video_id, all_variants)
    past_results = [vars(r) for r in db.get_analysis_results(task.video_id)]
    keyboard = get_analysis_menu_keyboard(task_id, all_variants, past_results)
    await status.edit_text(
        f"🎯 **{task.title}**\n\nВыберите тип анализа:",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


@router.callback_query(F.data.startswith("analyze_run:"))
async def cb_llm_analyze(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    task_id, slot_idx = parts[1], int(parts[2])
    task_data = _tasks.get(task_id)
    if not task_data:
        await callback.answer("❌ Задача не найдена.", show_alert=True)
        return

    task: MediaTask = task_data["task"]
    text: str = task_data.get("transcript", "")
    variants: list[dict] = task_data.get("variants", [{"idx": 1, "label": "Саммари", "prompt": "Сделай структурированное саммари видео: ключевые тезисы, неочевидные инсайты, итоговый вывод."}])

    chosen = next((v for v in variants if v["idx"] == slot_idx), variants[0])
    await callback.answer()

    prefix_llm = f"🧠 **{chosen['label']}**: {task.title}"
    await callback.message.edit_text(f"{prefix_llm}\n{len(text)} символов...", parse_mode="Markdown")

    done_llm = asyncio.Event()
    ticker_llm = asyncio.create_task(
        _ticker(callback, prefix_llm, total_sec=None, done_event=done_llm)
    )
    try:
        result = await llm_router.analyze(text, user_prompt=chosen["prompt"])
    except (ServiceDisabledError, LLMError) as e:
        await callback.message.edit_text(f"❌ {e.message}", parse_mode=None)
        return
    except Exception as e:
        logger.error(f"LLM analyze error: {e}")
        await callback.message.edit_text("❌ Ошибка при AI-анализе.")
        return
    finally:
        done_llm.set()
        ticker_llm.cancel()

    header = f"🧠 **{chosen['label']}: {task.title}**\n\n"
    for part in llm_router.split_for_telegram(header + result):
        await callback.message.answer(part, parse_mode="Markdown")

    gdrive_md_url: str | None = None

    # Сохраняем результат в БД и дописываем в .md
    try:
        db.add_analysis_result(task.video_id, chosen["label"], chosen["prompt"], result)
        db.append_llm_to_file(task.video_id, chosen["label"], chosen["prompt"], result)
        db.record_llm_call(task.video_id, chosen["prompt"])
        # Синхронизируем обновлённый .md на GDrive
        if ENABLE_GDRIVE:
            state = db.get_state(task.video_id)
            if state and state.transcript_path:
                updated_md = Path(state.transcript_path)
                if updated_md.exists():
                    try:
                        gdrive_sync = await gdrive.upload_transcript(updated_md)
                        db.set_gdrive_synced_after_llm(task.video_id)
                        gdrive_md_url = gdrive_sync.public_url
                    except Exception as e:
                        logger.warning(f"GDrive sync error: {e}")
    except Exception as e:
        logger.warning(f"DB update error after LLM: {e}")

    status_lines = [
        f"✅ **{chosen['label']}** завершён: **{task.title}**",
        f"📝 {len(text)} символов • 🧠 {len(result)} символов",
    ]
    if gdrive_md_url:
        status_lines.append(f"☁️ [Документ на GDrive]({gdrive_md_url})")

    await callback.message.edit_text(
        "\n".join(status_lines), parse_mode="Markdown", disable_web_page_preview=True,
    )
    logger.info(f"LLM analyze complete [{chosen['label']}]: {task.title}")


# YTS_bot (YouTube Station Bot)
Локальный Telegram-бот для парсинга, скачивания медиа (yt-dlp), умной транскрибации (Whisper) и семантического анализа (ChromaDB). 
Разработка ведется удаленно (через Web IDE), запуск и тестирование — локально. 
Ключевая особенность: строгая модульность и Feature Toggles (отключение любого модуля без падения системы).

## Стек
- **Язык:** Python 3.11+ (строгая асинхронность)
- **Бот:** `aiogram` 3.x (роутеры, FSM, Inline-меню)
- **Медиа:** `yt-dlp` (метаданные, загрузка), `ffmpeg` (локальная нарезка)
- **AI & LLM:** `groq` (Whisper API — основной), `httpx` (Omniroute — fallback транскрибации + LLM роутер)
- **Хранилище:** `google-api-python-client` (GDrive — медиа + транскрипты), внешняя KB (API, на будущее)

## Структура проекта
- `main.py` — Точка входа бота.
- `run_tests.py` — Скрипт диагностики окружения (API ключи, ffmpeg, права).
- `core/` — Фундамент:
  - `config.py` — Парсинг `.env` (включая флаги `ENABLE_...`).
  - `logger.py` — Настройка логгирования.
- `bot/` — UI слой (Telegram):
  - `handlers/` — Обработка команд.
  - `keyboards/` — Динамические меню (зависят от Feature Toggles).
- `services/` — Слой бизнес-логики:
  - `downloader.py` (yt-dlp), `transcriber.py` (Groq), `llm_router.py` (Omniroute), `gdrive.py` (GDrive upload).
- `utils/` — Хелперы (генерация MD, чанкинг текста).

## Ключевые сущности (Data Models)
- `MediaTask`: url, title, duration_sec, temp_file_path.
- `FeatureConfig`: Статус модулей (DOWNLOADER, TRANSCRIPT, LLM, KB, GDRIVE).

## Правила написания кода (Strict Rules)
1. **Feature Toggles:** Вся функциональность зависит от флагов в `config.py` (например, `config.ENABLE_TRANSCRIPT`). Если флаг `False`, кнопки этого модуля **не должны** рендериться в клавиатурах, а вызовы функций должны возвращать ошибку/заглушку.
2. **Изоляция сбоев (Resilience):** Падение одного модуля (например, API Groq лежит) не должно крашить бота. Ошибка перехватывается, логгируется, юзеру выдается сообщение, бот продолжает работать.
3. **Без `print()`:** Использовать только встроенный `logging` (`logger = logging.getLogger(__name__)`).
4. **Строгая типизация:** Обязательны Type Hints для всех аргументов и возвращаемых значений (`-> None`, `-> str` и т.д.).
5. **Никакой блокировки Event Loop:** Любые синхронные вызовы (`yt-dlp`, IO диска, `ffmpeg`) оборачивать в `asyncio.to_thread()`.

## Паттерны (Examples)

### Паттерн динамической клавиатуры с учетом флагов (Feature Toggles)
```python
def get_media_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if config.ENABLE_DOWNLOADER:
        builder.button(text="🎵 MP3", callback_data="dl_mp3")
    if config.ENABLE_TRANSCRIPT:
        builder.button(text="📝 Текст", callback_data="get_transcript")
    return builder.as_markup()
```

### Паттерн безопасного вызова блокирующей функции
```python
try:
    if not config.ENABLE_DOWNLOADER:
        raise ValueError("Модуль загрузчика отключен.")
    # Обертка блокирующего вызова
    file_path = await asyncio.to_thread(yt_service.download, url)
except Exception as e:
    logger.error(f"Download error: {e}")
    await message.answer(f"❌ Ошибка: {str(e)}")
```

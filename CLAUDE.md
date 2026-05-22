# YTS_bot (YouTube Station Bot)
Локальный Telegram-бот для парсинга, скачивания медиа (yt-dlp), умной транскрибации (Whisper) и AI-анализа (OmniRoute → Claude).
Разработка ведётся удалённо (через Web IDE), запуск и тестирование — локально.
Ключевая особенность: строгая модульность и Feature Toggles (отключение любого модуля без падения системы).

## 1. Окружение и стек
- **Язык:** Python 3.12 (строгая асинхронность)
- **Бот:** `aiogram` 3.x (роутеры, FSM, Inline-меню, ReplyKeyboard)
- **Медиа:** `yt-dlp` + bgutil POT-провайдер (HTTP, `[::1]:4416`), `ffmpeg` (нарезка)
- **AI & LLM:** `groq` (Whisper API — основной), `httpx` (OmniRoute/OpenRouter — LLM роутер, `stream=False`)
- **Хранилище:** SQLite (`data/history.db`), `google-api-python-client` (GDrive — медиа + транскрипты)

## 2. Команды запуска
1. Запустить POT-сервер: `cd C:\Users\yakov\bgutil-ytdlp-pot-provider\server && deno run --allow-all src/main.ts`
2. Запустить бота: `python main.py`
> Нельзя запускать два экземпляра бота одновременно — TelegramConflictError.

## 3. Структура проекта
→ см. `README.md` (полный список файлов и назначений)

Ключевые точки входа:
- `main.py` — точка входа, whitelist middleware, init_db
- `core/config.py` — Feature Toggles, ALLOWED_USER_IDS, DATA_DIR
- `bot/handlers/url_handler.py` — основной хэндлер + FSM
- `services/` — вся бизнес-логика (downloader, transcriber, llm_router, gdrive, history)

## 4. Ключевые сущности
- `MediaTask`: url, title, duration_sec, video_id, temp_file_path и метаданные.
- `VideoEntry` + `ProcessingState` в `services/db.py` — основные записи БД (заменили `HistoryEntry`).
- `_tasks: dict[str, dict]` в url_handler — RAM-хранилище активных задач (теряется при рестарте).
- БД: таблицы `videos`, `processing_state`, `analysis_results`, `analysis_variants` в `data/history.db`.

## 5. Стандарты кодирования (Strict Rules)
1. **Feature Toggles:** Флаги в `config.py` (`ENABLE_TRANSCRIPT` и др.). Если `False` — кнопки не рендерятся, вызовы возвращают заглушку.
2. **Изоляция сбоев:** Падение модуля не крашит бота. Ошибка перехватывается, логгируется, юзеру — сообщение.
3. **Без `print()`:** Только `logging.getLogger(__name__)`.
4. **Строгая типизация:** Type Hints обязательны везде.
5. **Никакой блокировки Event Loop:** Синхронные вызовы (yt-dlp, IO, ffmpeg) — через `asyncio.to_thread()`.
6. **ENABLE_KB не трогать:** Задел на будущее (RAG через Colab). Не путать с историей транскриптов.

## 6. Контроль расходов и выбор моделей (Model & Effort Advisor)

### Шпаргалка по задачам

| Задача | Модель | Effort (Thinking) |
| :--- | :--- | :--- |
| Рутина: коммит, чтение логов, поиск файла, простой вопрос | `claude-haiku-4-5` | Выключен |
| Стандартный кодинг: новая функция, тест, правка одного файла | `claude-sonnet-4-6` | Low / Medium |
| Сложный дебаг / алгоритм: баг в 2-3 файлах, оптимизация | `claude-sonnet-4-6` | High |
| Архитектура / рефакторинг >4 файлов / критический сбой | `claude-opus-4-7` | High / Max |

> **Про Effort:** в Claude Code я не управляю `thinking budget` программно. "Effort" — это совет тебе при прямых API-вызовах (например в `llm_router.py`) и ориентир для меня при оценке сложности задачи.

### Как это работает в сессии

При планировании многоэтапной задачи я **группирую шаги по модели** и перед каждой группой пишу:

> `⚠️ Для следующей группы задач оптимальна модель [X]. Пожалуйста, переключи: /model [X]`

Я не блокирую работу принудительно — ты можешь ответить "продолжай на текущей" и я продолжу. Но напоминание будет всегда.

Если текущая модель явно не соответствует задаче (например Haiku + переписать БД), я скажу об этом **первой строкой** перед планом.

## 7. Контроль архитектуры (Динамический триггер)
При старте каждой сессии провести быструю оценку размера проекта (LoC) и проверить соответствие структуры:
- **Микро (<500 LoC):** Только `CLAUDE.md`.
- **Малый (500–2k LoC):** `CLAUDE.md` + `CURRENT_STAGE.md`.
- **Средний (2k–10k LoC):** `CLAUDE.md` + `CURRENT_STAGE.md` + `docs/architecture.md`.
- **Большой (>10k LoC):** Все выше + модульные инструкции в `.claude/docs/`.

Если проект перерастает текущую категорию — сообщить первым сообщением и предложить рефакторинг документации.

## 7. Паттерны (Examples)

### Динамическая клавиатура с Feature Toggles
```python
def get_media_keyboard(task_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if features.downloader:
        builder.button(text="🎵 M4A", callback_data=f"dl_m4a:{task_id}")
    if features.transcript:
        builder.button(text="📝 Транскрибировать", callback_data=f"transcript:{task_id}")
    builder.adjust(2)
    return builder.as_markup()
```

### Безопасный вызов блокирующей функции
```python
try:
    if not ENABLE_DOWNLOADER:
        raise ServiceDisabledError("DOWNLOADER")
    file_path = await asyncio.to_thread(yt_service.download, url)
except Exception as e:
    logger.error(f"Download error: {e}")
    await message.answer(f"❌ Ошибка: {e}")
```

### Прогресс-тикер
```python
done = asyncio.Event()
ticker = asyncio.create_task(_ticker(callback, "⬇️ Скачиваю...", total_sec=30.0, done_event=done))
try:
    result = await long_operation()
finally:
    done.set()
    ticker.cancel()
```

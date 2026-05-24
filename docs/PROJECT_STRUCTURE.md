# Структура проекта YTscribe Smart

Карта репозитория для быстрой навигации (человек / агент).  
**Source of truth транскриптов:** JSON v1.0 в `data/transcripts/` (см. `docs/json_schema_v1.md`).

---

## Дерево каталогов

```
YTscribe_Smart/
│
├── main.py                    # Точка входа бота (polling)
├── bot_service.bat              # Лаунчер: start | stop | restart | status | меню
├── run_tests.py                 # Диагностика окружения (.env, ffmpeg, ключи)
├── requirements.txt
│
├── README.md                    # Быстрый старт
├── CLAUDE.md                    # Правила для AI-агентов при правке кода
├── CURRENT_STAGE.md             # Roadmap и что уже сделано
├── LICENSE
│
├── core/                        # Ядро: конфиг, модели, логи, исключения
│   ├── config.py                # .env, Feature Toggles, пути
│   ├── models.py                # MediaTask и dataclass-ы
│   ├── logger.py                # setup_logging, is_headless
│   └── exceptions.py            # ServiceDisabledError, DownloadError, …
│
├── bot/                         # Telegram UI (aiogram 3)
│   ├── handlers/                # Роутеры callback/message
│   │   ├── start.py             # /start, inline-панель, история, настройки
│   │   ├── url_handler.py       # YouTube URL, карточка, AI, FSM чата
│   │   ├── batch_handler.py     # Пакетная транскрибация
│   │   └── gdrive_sync_handler.py  # Экран ☁️ GDrive sync
│   ├── keyboards/               # Inline-клавиатуры (menu:*, nav:*, …)
│   ├── middleware/              # Resilience, уведомления об ошибках
│   ├── pipeline/                # Оркестрация download → transcribe → save
│   │   ├── media.py
│   │   └── transcript.py
│   ├── ui/                      # Экраны, прогресс, панель, safe_edit
│   └── utils/
│       └── chat_delivery.py     # Отправка файлов/частей в чат
│
├── services/                    # Бизнес-логика (без Telegram)
│   ├── db.py                    # SQLite: videos, processing_state, AI results
│   ├── downloader.py            # yt-dlp + POT HTTP + cookies
│   ├── transcriber.py           # Groq Whisper + fallback
│   ├── llm_router.py            # OmniRoute LLM
│   ├── llm_persist.py           # Сохранение AI в БД + JSON
│   ├── ai_chat.py               # Диалог по теме анализа (retrieval)
│   ├── auto_summary.py          # Авто-саммари после транскрипта
│   ├── gdrive.py                # Upload / local sync папки Drive
│   ├── gdrive_sync.py           # Audit / repair JSON ↔ Drive
│   ├── transcript_json.py       # CRUD JSON-транскриптов
│   ├── transcript_migrate.py    # On-demand MD → JSON
│   ├── transcript_paths.py      # Поиск файла по video_id
│   ├── transcript_storage.py      # Пути каталогов transcripts
│   ├── task_session.py          # In-memory сессии (key=video_id, rehydrate)
│   ├── cookie_manager.py        # Cookies из браузера / cookies.txt
│   ├── user_settings.py         # Per-user Whisper/LLM/язык/toggles
│   ├── llm_cache.py             # Кеш LLM в data/llm_cache/
│   └── timing_stats.py          # Метки этапов для прогресса
│
├── utils/                       # Чистые хелперы (без services/bot)
│   ├── json_format.py           # Schema v1.0: build/load/validate
│   ├── url_parser.py            # YouTube URL, video_id
│   ├── media_chunker.py         # ffmpeg: нарезка >25MB
│   ├── batch_import.py          # Импорт .txt / zip для пакета
│   ├── history_format.py        # Подписи в списке истории
│   ├── telegram_format.py       # HTML escape для Telegram
│   └── md_format.py             # LEGACY: чтение старых .md
│
├── scripts/
│   ├── bot_service_menu.ps1     # POT + бот в фоне, PID, логи
│   ├── gdrive_sync_cli.py       # audit / repair из терминала
│   ├── migrate_md_to_json.py    # Массовая миграция .md → JSON
│   ├── rebuild_ai.py            # Сброс и пересборка AI в БД/JSON
│   ├── install_omniroute.bat    # (опционально) npm install omniroute CLI
│   └── legacy/                  # Устаревшие .md-утилиты
│
├── docs/
│   ├── PROJECT_STRUCTURE.md     # ← этот файл
│   ├── architecture.md          # Потоки данных (pipeline A–D)
│   └── json_schema_v1.md        # Спецификация JSON
│
├── data/                        # RUNTIME (в .gitignore)
│   ├── history.db               # SQLite
│   ├── transcripts/             # *.json (+ old/ для legacy .md)
│   └── llm_cache/
│
├── temp/                        # Скачанное yt-dlp (очистка при старте)
├── .run/                        # Логи и PID (yts_bot.log, service.log)
├── credentials/                 # Секреты GDrive (gitignore)
├── cookies.txt                  # Fallback cookies (gitignore)
├── venv/                        # Python venv (gitignore)
└── .env                         # Конфиг (gitignore), образец: .env.example
```

---

## Внешние зависимости (не в репозитории)

| Компонент | Где живёт | Зачем |
|-----------|-----------|--------|
| **POT-сервер** | `POT_SERVER_DIR` в `.env` (bgutil-ytdlp-pot-provider) | PO Token для yt-dlp |
| **OmniRoute** | Desktop-приложение, порт `OMNIROUTE_PORT` | LLM / Whisper fallback |
| **deno** | PATH | Запуск POT |
| **ffmpeg** | PATH | Нарезка длинного аудио |
| **GDrive Desktop** | `GDRIVE_LOCAL_DIR` | Локальное зеркало JSON |

---

## Куда смотреть по задаче

| Задача | Файлы |
|--------|--------|
| Новая кнопка / экран Telegram | `bot/keyboards/main_menu.py`, `bot/ui/screens.py`, `bot/handlers/*.py` |
| Callback «устарел» / video_id | `services/task_session.py`, `bot/handlers/url_handler.py` |
| Скачивание YouTube | `services/downloader.py`, `services/cookie_manager.py` |
| Транскрипт → JSON | `services/transcript_json.py`, `utils/json_format.py` |
| AI-анализ / чат | `services/llm_router.py`, `services/ai_chat.py`, `url_handler.py` |
| История / настройки | `bot/handlers/start.py` |
| GDrive sync | `services/gdrive_sync.py`, `gdrive_sync_handler.py` |
| Запуск в фоне Windows | `bot_service.bat`, `scripts/bot_service_menu.ps1` |
| Миграция MD → JSON | `scripts/migrate_md_to_json.py`, `services/transcript_migrate.py` |
| Feature toggle | `core/config.py` → `features.*` |

---

## Поток данных (кратко)

```
URL → downloader (yt-dlp+POT) → temp/m4a
    → transcriber (Groq) → transcript_json → data/transcripts/*.json
    → llm_router → llm_persist → ai_analysis[] в JSON
    → gdrive (local folder) → Google Drive Desktop
```

Подробнее: `docs/architecture.md`.

---

## Слои и правила

1. **`bot/`** — только Telegram: handlers, UI, клавиатуры. Не ходит в yt-dlp напрямую.
2. **`services/`** — логика без aiogram. Можно вызывать из scripts.
3. **`utils/`** — функции без состояния и без импорта `services` (кроме редких CLI).
4. **`bot/pipeline/`** — glue между handlers и services (progress + save).
5. **Callbacks** — везде `video_id` (11 символов), не ephemeral task_id.
6. **Логи** — `logging`, не `print()` (CLI-скрипты — исключение).

---

## Скрипты

| Команда | Назначение |
|---------|------------|
| `bot_service.bat start` | POT (если нужен) + бот в фоне |
| `bot_service.bat stop` | Остановить бот + POT |
| `bot_service.bat status` | PID, порты, пути логов |
| `python run_tests.py` | Проверка .env / ffmpeg / ключей |
| `python scripts/gdrive_sync_cli.py audit` | Аудит sync |
| `python scripts/migrate_md_to_json.py --yes` | MD → JSON |
| `python scripts/rebuild_ai.py --dry-run` | План пересборки AI |

---

## Runtime-артефакты (не коммитить)

- `data/`, `temp/`, `.run/`, `venv/`, `.env`, `cookies.txt`
- `credentials/`, `client_secret_*.json` в корне — перенести в `credentials/` или удалить
- `data/transcripts/old/` — архив legacy `.md` после миграции
- `__pycache__/` — артеfact Python

---

## Что удалено / legacy

| Было | Статус |
|------|--------|
| `services/history.py` | Удалён (дублировал `db.py`) |
| `utils/md_generator.py` | Удалён |
| `services/md_sync.py` | → `scripts/legacy/md_sync.py` |
| `run_omniroute.bat` | Удалён (OmniRoute — desktop app; см. `bot_service.bat`) |
| `.md` как source of truth | Только `utils/md_format.py` для fallback-чтения |

---

## Документы для агентов

1. **Навигация** — этот файл (`docs/PROJECT_STRUCTURE.md`)
2. **Правила кода** — `CLAUDE.md`
3. **Текущий статус** — `CURRENT_STAGE.md`
4. **JSON schema** — `docs/json_schema_v1.md`

При новой задаче достаточно прочитать пункт 1 + релевантный handler/service из таблицы «Куда смотреть».

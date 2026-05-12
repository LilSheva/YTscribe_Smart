# YTscribe_Smart (YTS_bot)

Локальный Telegram-бот для парсинга, скачивания медиа, умной транскрибации и AI-аналитики YouTube-видео. Работает на домашнем ПК, обеспечивает приватность данных и бесплатную интеграцию с AI-инструментами.

## Основные возможности

- **Скачивание медиа** — M4A/MP4 через yt-dlp с авто-загрузкой на Google Drive
- **Транскрибация** — Groq Whisper API (основной) + OmniRoute (fallback), RU+EN
- **AI-аналитика** — саммари, разбор, экшен-поинты через Claude 3.5 / Haiku (OmniRoute)
- **Автосохранение** — транскрипты как .md с полными метаданными → Google Drive
- **Модульность** — Feature Toggles: любой модуль отключается без падения бота

---

## Требования

| Компонент | Версия | Назначение |
|-----------|--------|-----------|
| Python | 3.11+ | Основной язык |
| FFmpeg | любая | Нарезка аудио > 25MB |
| Node.js | 18+ | Запуск OmniRoute (LLM gateway) |
| Google Chrome | любая | Cookies для обхода блокировок YouTube |

---

## Первый запуск (Windows)

### 1. Клонирование и зависимости

```bash
git clone https://github.com/LilSheva/YTscribe_Smart.git
cd YTscribe_Smart
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 2. FFmpeg

Скачайте с [ffmpeg.org](https://ffmpeg.org/download.html), распакуйте и добавьте папку `bin/` в системную переменную PATH.

Проверка: `ffmpeg -version`

### 3. OmniRoute (AI Gateway)

```bash
scripts\install_omniroute.bat
```

Скрипт проверит Node.js и установит OmniRoute глобально.

### 4. Настройка .env

```bash
copy .env.example .env
```

Откройте `.env` и заполните:

```env
# Обязательные
BOT_TOKEN=ваш_токен_от_BotFather
GROQ_API_KEY=ваш_ключ_от_console.groq.com
OMNIROUTE_API_KEY=ваш_ключ_из_дашборда_omniroute

# Google Drive (если нужна загрузка файлов)
GDRIVE_MEDIA_FOLDER_ID=id_папки_для_медиа
GDRIVE_TRANSCRIPTS_FOLDER_ID=id_папки_для_транскриптов
```

### 5. Google Drive (опционально)

1. Создайте сервисный аккаунт в [Google Cloud Console](https://console.cloud.google.com/)
2. Включите Google Drive API
3. Скачайте JSON-ключ → сохраните как `credentials/gdrive_service.json`
4. Расшарьте нужные папки на email сервисного аккаунта
5. Скопируйте ID папок (из URL) в `.env`

Если не нужен GDrive — поставьте `ENABLE_GDRIVE=False` в `.env`.

### 6. Проверка окружения

```bash
python run_tests.py
```

Скрипт проверит: .env, токены, ffmpeg, temp/, GDrive credentials.

### 7. Запуск

**Вариант A (рекомендуемый):** OmniRoute + бот одной командой:
```bash
scripts\run_omniroute.bat
```

**Вариант B:** Только бот (если OmniRoute уже запущен или не нужен):
```bash
python main.py
```

---

## Использование

1. Откройте бота в Telegram
2. Отправьте ссылку на YouTube-видео
3. Бот покажет карточку с метаданными и кнопки:
   - **🎵 M4A / 🎬 MP4** — скачать → GDrive → ссылка в чат
   - **📝 Транскрибировать** — аудио → Whisper → текст + .md на GDrive
   - **🧠 AI Саммари** — транскрипт → Claude → структурированный анализ

Команды:
- `/start` — приветствие
- `/help` — список команд
- `/search запрос` — поиск по базе знаний (когда подключена)

---

## Структура проекта

```
├── main.py                  — Точка входа
├── run_tests.py             — Диагностика окружения
├── core/                    — Конфиг, логгер, модели, исключения
├── bot/handlers/            — Telegram-хэндлеры (start, url_handler)
├── bot/keyboards/           — Инлайн-клавиатуры (Feature Toggles)
├── services/                — Бизнес-логика (downloader, transcriber, llm, gdrive)
├── utils/                   — Хелперы (md_generator, media_chunker)
├── scripts/                 — Bat-скрипты для Windows
└── docs/                    — Архитектура и документация
```

---

## Feature Toggles

В `.env` можно включить/отключить любой модуль:

```env
ENABLE_DOWNLOADER=True    # yt-dlp загрузка
ENABLE_TRANSCRIPT=True    # Whisper транскрибация
ENABLE_LLM=True           # AI-аналитика (OmniRoute)
ENABLE_GDRIVE=True        # Загрузка на Google Drive
ENABLE_KB=False           # Внешняя база знаний (на будущее)
```

Отключённый модуль: кнопки не показываются, вызовы возвращают заглушку, бот продолжает работать.

---

## Документация

- `CLAUDE.md` — правила написания кода (для ИИ-ассистентов)
- `CURRENT_STAGE.md` — трекер задач и текущий статус
- `docs/architecture.md` — архитектура, слои, потоки данных

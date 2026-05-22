# Текущее состояние проекта
Обновлено: 2026-05-21

## 1. Что уже сделано и протестировано

### Фаза 1 — Core & MVP Загрузчик
- [x] Базовая инфраструктура: `core/config.py` (`.env`, Feature Toggles, ALLOWED_USER_IDS)
- [x] Каркас бота: `aiogram 3.x` в `main.py`, whitelist middleware
- [x] Базовые хэндлеры: `bot/handlers/start.py` (ReplyKeyboard, UI истории)
- [x] Модуль yt-dlp: `services/downloader.py` (POT bgutil, cookies.txt, progress_hooks)
- [x] Динамическая клавиатура: `bot/keyboards/main_menu.py` (inline + reply, Feature Toggles)

### Фаза 2 — Smart Processing
- [x] Транскрибация: Groq Whisper API (`services/transcriber.py`)
- [x] Нарезка аудио >25MB через ffmpeg (`utils/media_chunker.py`)
- [x] LLM-аналитика: OmniRoute / OpenRouter (`services/llm_router.py`, `stream=False`)
- [x] FSM для обновления вариантов анализа (`bot/handlers/url_handler.py`)
- [x] PO Token провайдер (bgutil, HTTP `[::1]:4416`, web+mweb клиенты)
- [x] Поддержка cookies.txt (приоритет над браузерными куками)

### Фаза 3 — Storage & Export
- [x] GDrive upload: медиа + транскрипты, раздельные папки (`services/gdrive.py`)
- [x] SQLite история: транскрипты, варианты анализа (`services/history.py`)
- [x] Генерация .md транскриптов (`utils/md_generator.py`)
- [x] Замена ChromaDB на внешнюю KB-заглушку (`ENABLE_KB` — задел на будущее)

### Исправленные баги
- [x] HTML parse_mode в `start.py` (фикс падения на Markdown-спецсимволах)
- [x] Отключён parse_mode в сообщениях об ошибках
- [x] LLM default переключён на OpenRouter
- [x] Транскрипт показывает только GDrive-ссылку (без дампа чата)

---

## 2. Что сейчас в работе

*(нет активной задачи — ожидание)*

---

## 3. Следующий шаг (Этап 5 — UX)

- [ ] Кнопки «◀️ Назад» везде где есть вложенные экраны
- [ ] Единый стиль сообщений по всему боту
- [ ] Убрать эмодзи из `logger.info()` (cp1251 крашит консоль Windows)

---

## 4. Важные файлы для текущего контекста

- `bot/handlers/url_handler.py` — основной хэндлер, FSM
- `bot/handlers/start.py` — ReplyKeyboard, история
- `bot/keyboards/main_menu.py` — все клавиатуры
- `core/config.py` — Feature Toggles, окружение
- `services/llm_router.py` — LLM роутинг

---

## 5. Бэклог (Будущие этапы)

### Этап 6 — Пакетная обработка ссылок
- [ ] Несколько ссылок в одном сообщении → очередь с прогрессом по каждой
- [ ] Итоговый отчёт по пакету

### Этап 7 — Рефакторинг и единая точка запуска
- [ ] Единый `.bat`: POT-сервер в фоне + бот
- [ ] Вынести дублирующуюся логику в `_ensure_transcript(task_data)`
- [ ] Аудит и единый стиль кода

### Этап 8 — Меню настроек
- [ ] Выбор модели транскрипции и LLM per-user (SQLite)
- [ ] Язык транскрипции: авто / ru / en

---

## 6. Идеи (обсудить позже)

- Автоглавы: LLM разбивает транскрипт на главы с таймкодами
- Q&A по видео: пользователь задаёт вопрос, LLM отвечает по транскрипту
- Сравнение двух видео из истории
- Obsidian-совместимый .md с frontmatter
- Персистентный `_tasks` в SQLite (переживает рестарт)
- Streaming LLM ответов через `edit_text`
- Оценка качества ответов: 👍/👎 → сохранять в БД
- Fallback chain для cookies: Chrome → Brave → Edge → Chromium → cookies.txt
- Опция `--impersonate` для имитации браузера (против бот-детекта)

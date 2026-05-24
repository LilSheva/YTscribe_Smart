# Текущее состояние проекта YTscribe Smart

Обновлено: 2026-05-24 (JSON v1.0, GDrive local, retrieval)

**Карта каталогов:** `docs/PROJECT_STRUCTURE.md`

---

## 1. Цель проекта

Telegram-бот для YouTube:

1. Скачать / распознать речь (Groq Whisper + fallback)
2. Сохранить **структурированный JSON** (source of truth для ингеста)
3. AI-анализ (OmniRoute) + авто-саммари
4. Синхронизация с **Google Drive Desktop** (`GDRIVE_MODE=local`)
5. (Будущее) PKH/KB, чат по теме анализа с retrieval

---

## 2. Архитектура данных

```
YouTube URL
    → yt-dlp (метаданные + аудио)
    → Whisper (транскрипт)
    → JSON файл на диске + SQLite (индекс / флаги)
    → LLM → ai_analysis[] в JSON
    → (будущее) chat.messages[] внутри каждой темы
    → Google Drive Desktop синхронизирует GDRIVE_LOCAL_DIR
```

| Слой | Роль |
|------|------|
| **JSON** (`*.json`) | Source of truth для ингеста |
| **SQLite** (`data/history.db`) | Дедуп, флаги, `analysis_results`, user settings |
| **`data/llm_cache/`** | Кеш повторных LLM-запросов |
| **GDrive local** | `GDRIVE_LOCAL_DIR` — зеркало в облаке |

### JSON schema v1.0 (утверждено, реализовано)

```json
{
  "schema_version": "1.0",
  "source": "ytscribe_smart",
  "video": {
    "video_id", "title", "url", "channel", "duration_sec",
    "upload_date", "language", "added_at", "added_by_user_id"
  },
  "transcript": { "text", "processed_at" },
  "ai_analysis": [{
    "id", "label", "prompt", "model", "created_at",
    "body": "markdown string",
    "chat": {
      "context_policy": "retrieval",
      "messages": [{ "id", "role", "content", "created_at", "model?" }],
      "updated_at"
    }
  }],
  "sync": { "gdrive_path", "gdrive_synced_at" }
}
```

- **Имя файла:** `{video_id}_{полное название}.json`
- **Legacy `.md`:** переносятся в `YT_transcribe/old/` (не source of truth)
- **`body` / `chat.messages[].content`:** markdown внутри JSON-строки

### Контекст чата (retrieval)

| `context_policy` | Что уходит в LLM |
|------------------|------------------|
| `retrieval` (default) | `body` + история + top-3 фрагмента `transcript.text` |
| `summary_only` | только `body` + история |
| `full_transcript` | весь транскрипт (явно) |

Первый ответ по теме = `body`; `messages` — только продолжение диалога.

---

## 3. Roadmap — этапы A–H

| # | Этап | Статус |
|---|------|--------|
| **A** | Детальный прогресс (`ProgressReporter`) | **готово** |
| **B** | Inline-навигация (edit + delete) | **готово** |
| **C** | Toggles: транскрипт / AI в чат | **готово** |
| **D** | Пакетная обработка (.txt, multi-URL, 📦 Пакет) | **готово** |
| **E** | GDrive sync (audit / repair) | **готово** *(UI прогресс — §5.1)* |
| **F** | UX: «Назад», `media_header`, ошибки | **готово** |
| **G** | PKH / KB API | **отложено** |
| **H** | Per-user настройки (Whisper, LLM, язык) | **готово** |

---

## 4. Сделано (фазы + последние сессии)

### 4.1 Базовый пайплайн (A–H)

- [x] `ProgressReporter` — этапы, heartbeat, ошибки в якоре
- [x] Inline UI, toggles чата, per-user settings
- [x] Batch: `.txt`, несколько URL, 📦 Пакет, HTML progress (fix Markdown crash)
- [x] GDrive sync: audit / repair / кнопки в боте
- [x] `media_header`, retry keyboards, logger без emoji

### 4.2 Авто-саммари и LLM

- [x] `AUTO_LLM_SUMMARY` — саммари после транскрибации
- [x] `LLM_CACHE_ENABLED` — кеш в `data/llm_cache/`
- [x] Промпты: markdown в `body`, без `#`/`##` в ответе (legacy MD этап)

### 4.3 Google Drive local mode

- [x] `GDRIVE_MODE=local` + `GDRIVE_LOCAL_DIR`
- [x] `ensure_in_sync_folder` / `migrate` — копия в папку Drive Desktop
- [x] Repair обновляет пути и `sync` в JSON
- [x] Кнопки: **🔧 Исправить sync**, **🧠 Добить саммари**

### 4.4 Переход MD → JSON (2026-05-24)

- [x] `utils/json_format.py` — schema v1.0, validate, save/load
- [x] `services/transcript_json.py` — CRUD, AI, chat structure
- [x] `services/context_retrieval.py` — retrieval фрагментов транскрипта
- [x] `services/ai_chat.py` — логика чата (**без UI в боте**)
- [x] `scripts/migrate_md_to_json.py` — миграция + `old/` (**8 видео мигрировано**)
- [x] `services/transcript_migrate.py` — on-demand MD→JSON при чтении
- [x] Новые транскрипты → только `.json`
- [x] `llm_persist` → БД + JSON + sync

### 4.5 Callbacks + pipeline (2026-05-24)

- [x] Callbacks по **`video_id`** (не ephemeral task_id) — кнопки работают после рестарта
- [x] `services/task_session.py` — rehydrate сессии из БД
- [x] `bot/pipeline/` — download / transcribe / save (вынесено из url_handler)
- [x] Inline-панель: история / настройки / GDrive через `safe_edit_text`
- [x] Удалено: `services/history.py`, `utils/md_generator.py`

**Legacy (read-only fallback):** `utils/md_format.py`, `scripts/normalize_md.py`

---

## 5. Backlog — приоритеты

### 5.1 P1 — GDrive sync UI

- [ ] **ProgressReporter** в ☁️ GDrive sync: audit `[i/N]`, repair по видео
- [x] ~~AI не на Drive~~ — JSON + migrate + repair + local folder
- [x] ~~Файлы не в GDRIVE_LOCAL_DIR~~ — `migrate` / `ensure_in_sync_folder`
- [ ] Audit без ложных `stale_after_llm` сразу после LLM (регрессионная проверка)

**Файлы:** `bot/handlers/gdrive_sync_handler.py`, `services/gdrive_sync.py`

### 5.2 P2 — Чат по теме AI (§11)

- [x] `services/ai_chat.run_analysis_chat()` + retrieval
- [x] `chat` в JSON под каждым `ai_analysis[]`
- [ ] Кнопка **💬 Спросить** на экране AI-результата
- [ ] FSM / inline-thread в якорном сообщении
- [ ] `sync_transcript_storage` после каждого turn чата
- [ ] (Опционально) переключатель `full_transcript` в UI

**Файлы:** `bot/handlers/url_handler.py`, `services/ai_chat.py`, keyboards

### 5.3 P3 — UX чата и история (§2.9)

- [ ] История: **номер + дата/время** в списке (`12. 23.05 14:32 — Название`)
- [ ] Анти-спам: один якорь, TTL ephemeral, batch/sync без flood
- [ ] Рефакторинг: общий pipeline (single URL / batch / history / sync)
- [ ] Аудит `requirements.txt`, мёртвый код, legacy MD-модули

**Файлы:** `bot/handlers/start.py`, `bot/ui/nav.py`, handlers

### 5.4 P4 — Ингест / внешний проект

- [x] JSON v1.0 стабилен для парсера
- [ ] `docs/json_schema_v1.md` или `schema.json` для второго проекта
- [ ] PKH/KB (`ENABLE_KB`) — после стабильного UX

---

## 6. Идеи без срока

- Саммари-for-саммари (сжатие длинного AI в чате)
- Embeddings вместо keyword retrieval для чата
- Автоглавы с таймкодами (поле в JSON?)
- Сравнение двух видео
- Streaming LLM
- 👍/👎 на ответы
- Obsidian export (если понадобится — из JSON, не MD)

---

## 7. CLI / скрипты

| Команда | Назначение |
|---------|------------|
| `python scripts/migrate_md_to_json.py --yes` | MD → JSON, `.md` в `old/` |
| `python scripts/gdrive_sync_cli.py audit` | Аудит sync |
| `python scripts/gdrive_sync_cli.py repair` | Repair проблем |
| `python scripts/gdrive_sync_cli.py migrate` | Копия в `GDRIVE_LOCAL_DIR` |
| `python scripts/rebuild_ai.py --yes --clear-cache` | Сброс AI + перегенерация саммари |
| `bot_service_menu.ps1` → **10** | GDrive sync из меню |

---

## 8. Важные файлы

| Файл | Роль |
|------|------|
| `utils/json_format.py` | JSON schema v1.0 |
| `services/transcript_json.py` | CRUD JSON |
| `services/context_retrieval.py` | Retrieval для чата |
| `services/ai_chat.py` | Чат по `ai_analysis.id` |
| `services/llm_persist.py` | LLM → БД + JSON + sync |
| `services/auto_summary.py` | Авто-саммари после STT |
| `services/gdrive_sync.py` | Audit / repair |
| `services/transcript_paths.py` | Поиск `.json` |
| `services/llm_cache.py` | Кеш LLM |
| `bot/handlers/url_handler.py` | Основной пайплайн |
| `bot/handlers/gdrive_sync_handler.py` | UI GDrive sync |
| `bot/ui/progress.py` | ProgressReporter |
| `core/config.py` | `.env`, toggles |

---

## 9. Рекомендуемый порядок реализации

**Сделано:** A → B → C → D → E → F → H → JSON v1.0 → GDrive local fix

**Дальше:**

1. **P2** — UI чата «💬 Спросить» + retrieval
2. **P1** — прогресс в GDrive sync screen
3. **P3** — история с датой, анти-спам, рефакторинг
4. **P4** — документация схемы для ингеста
5. **G** — PKH/KB

---

## 10. Критерии «готово к ингесту»

- [x] Один формат — JSON v1.0
- [x] Фиксированная структура (`video`, `transcript`, `ai_analysis`, `sync`)
- [x] Файлы в `GDRIVE_LOCAL_DIR`, синхронизация через Drive Desktop
- [x] `body` — markdown в строке, парсер не зависит от заголовков документа
- [ ] Документация схемы для внешнего проекта
- [ ] UI чата (для продукта, не блокер ингеста)

---

## 11. Справка — этапы A–H (детали, реализовано)

<details>
<summary>Этап A — ProgressReporter</summary>

- [x] `bot/ui/progress.py` — этап, подэтап, %, heartbeat >30с
- [x] yt-dlp, ffmpeg, Groq/OmniRoute, GDrive, LLM в progress UI
- [x] Ошибки в том же сообщении

</details>

<details>
<summary>Этап B — Inline-навигация</summary>

- [x] Якорное сообщение, edit вместо flood
- [x] «◀️ Назад» / «◀️ К видеo» / ephemeral cleanup
- [x] FSM «Уточнить» с cancel

</details>

<details>
<summary>Этап C — Toggles чата</summary>

- [x] `SHOW_TRANSCRIPT_IN_CHAT` / `SHOW_LLM_IN_CHAT` + per-user SQLite
- [x] «📤 Транскрипт в чат» / «📤 AI в чат»

</details>

<details>
<summary>Этап D — Пакетная обработка</summary>

- [x] `.txt` до 200 URL, multi-URL в сообщении, 📦 Пакет
- [x] Skip если в БД, отчёт `[i/N]`

</details>

<details>
<summary>Этап E — GDrive sync</summary>

- [x] `audit` / `repair` / `repair_all`
- [x] Проверки: missing, stale, json_llm_mismatch, outside_sync_folder, invalid_json
- [x] Бот: ☁️ GDrive sync, 🔧 Исправить, 🧠 Добить саммари
- [ ] ProgressReporter в sync UI (P1)

</details>

<details>
<summary>Этап F — UX</summary>

- [x] `media_header()`, retry keyboards, ◀️ Закрыть
- [x] HTML errors, logger без emoji

</details>

<details>
<summary>Этап H — Per-user settings</summary>

- [x] Whisper / LLM / язык / toggles в SQLite
- [x] Меню ⚙️ Настройки, сброс к defaults

</details>

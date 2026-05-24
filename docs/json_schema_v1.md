# JSON Schema v1.0 — YTscribe Smart

Канонический формат хранения транскрипта, AI-анализа и чата по теме.  
Файл — **единственный source of truth**; экспорт в Markdown не используется.

## Имя файла

```
{video_id}_{sanitized_title}.json
```

- `video_id` — 11-символьный ID YouTube
- `sanitized_title` — заголовок без символов `<>:"/\|?*`

Пример: `dQw4w9WgXcQ_Never Gonna Give You Up.json`

## Корневая структура

| Поле | Тип | Описание |
|------|-----|----------|
| `schema_version` | `"1.0"` | Версия схемы |
| `source` | `"ytscribe_smart"` | Идентификатор приложения |
| `video` | object | Метаданные видео |
| `transcript` | object | Текст транскрипта |
| `ai_analysis` | array | Список AI-анализов |
| `sync` | object | Метаданные синхронизации с Drive |

## Пример документа

```json
{
  "schema_version": "1.0",
  "source": "ytscribe_smart",
  "video": {
    "video_id": "dQw4w9WgXcQ",
    "title": "Never Gonna Give You Up",
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "channel": "Rick Astley",
    "duration_sec": 213,
    "upload_date": "2009-10-25",
    "language": "en",
    "added_at": "2026-05-23T14:30:00",
    "added_by_user_id": 123456789
  },
  "transcript": {
    "text": "We're no strangers to love…",
    "processed_at": "2026-05-23T14:31:05"
  },
  "ai_analysis": [
    {
      "id": 1,
      "label": "Саммари",
      "prompt": "Сделай структурированное саммари видео.",
      "model": "gpt-4o-mini",
      "created_at": "2026-05-23T14:32:00",
      "body": "#### Основные тезисы\n\n…",
      "chat": {
        "context_policy": "retrieval",
        "messages": [
          {
            "id": 1,
            "role": "user",
            "content": "Какие аргументы автор приводит?",
            "created_at": "2026-05-23T15:00:00"
          },
          {
            "id": 2,
            "role": "assistant",
            "content": "#### Ответ\n\n…",
            "model": "gpt-4o-mini",
            "created_at": "2026-05-23T15:00:12"
          }
        ],
        "updated_at": "2026-05-23T15:00:12"
      }
    }
  ],
  "sync": {
    "gdrive_path": "Z:/Мой диск/YT_transcribe/dQw4w9WgXcQ_Never Gonna Give You Up.json",
    "gdrive_synced_at": "2026-05-23T15:00:15"
  }
}
```

## Блок `video`

| Поле | Тип | Обязательно |
|------|-----|-------------|
| `video_id` | string | да |
| `title` | string | да |
| `url` | string | да |
| `channel` | string | да |
| `duration_sec` | integer | да |
| `upload_date` | string | нет |
| `language` | string | нет |
| `added_at` | ISO 8601 datetime | да |
| `added_by_user_id` | integer | да |

## Блок `transcript`

| Поле | Тип | Описание |
|------|-----|----------|
| `text` | string | Полный текст транскрипта |
| `processed_at` | ISO 8601 datetime | Когда создан/обновлён |

## Блок `ai_analysis[]`

Каждый элемент — один запуск LLM (саммари, кастомный анализ и т.д.).

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | integer | Уникальный ID в рамках видео (совпадает с `analysis_results.id` в БД бота) |
| `label` | string | Короткое название («Саммари», «Ключевые идеи») |
| `prompt` | string | Промпт, отправленный в LLM |
| `model` | string | Идентификатор модели |
| `created_at` | ISO 8601 datetime | Время создания |
| `body` | string | Ответ LLM в **Markdown** (только `####` для подзаголовков) |
| `chat` | object | Диалог по этой теме |

### Блок `chat`

| Поле | Тип | Описание |
|------|-----|----------|
| `context_policy` | enum | Как собирается контекст для LLM (см. ниже) |
| `messages` | array | История сообщений user/assistant |
| `updated_at` | ISO 8601 datetime \| null | Последнее обновление чата |

#### `context_policy`

| Значение | Поведение |
|----------|-----------|
| `retrieval` | **По умолчанию.** Keyword-retrieval по транскрипту + тело анализа + история чата |
| `summary_only` | Только `body` анализа + история чата |
| `full_transcript` | Полный транскрипт + анализ + история (для одного ответа или явного режима) |

#### `chat.messages[]`

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | integer | Порядковый ID в рамках чата |
| `role` | `"user"` \| `"assistant"` | Роль |
| `content` | string | Текст сообщения (markdown для assistant) |
| `created_at` | ISO 8601 datetime | Время |
| `model` | string | Только для `assistant` — модель ответа |

## Блок `sync`

| Поле | Тип | Описание |
|------|-----|----------|
| `gdrive_path` | string | Локальный путь или URL файла на Drive |
| `gdrive_synced_at` | ISO 8601 datetime \| null | Последняя успешная синхронизация |

## Валидация

Реализация: `utils/json_format.py` → `validate_document()`.

Минимальные проверки:

- `schema_version == "1.0"`
- `source == "ytscribe_smart"`
- Обязательные поля `video`, `transcript`, `ai_analysis`, `sync`
- У каждого `ai_analysis[]`: уникальный `id`, валидный `context_policy`
- У сообщений чата: `role` ∈ {`user`, `assistant`}

## Интеграция для внешних проектов

1. Читайте JSON напрямую из папки синхронизации (`GDRIVE_LOCAL_DIR` или Drive API).
2. Для поиска по видео: glob `{video_id}_*.json`.
3. AI-ответы — в `ai_analysis[].body`; follow-up вопросы — в `ai_analysis[].chat.messages`.
4. Не полагайтесь на legacy `.md` — они переносятся в `old/` после миграции.

## Связанные модули (репозиторий бота)

| Модуль | Назначение |
|--------|------------|
| `utils/json_format.py` | Сборка, сохранение, валидация |
| `services/transcript_json.py` | CRUD, append AI, append chat |
| `services/context_retrieval.py` | Retrieval-контекст для чата |
| `services/ai_chat.py` | Отправка сообщения в чат по теме |
| `scripts/migrate_md_to_json.py` | Миграция legacy Markdown → JSON |

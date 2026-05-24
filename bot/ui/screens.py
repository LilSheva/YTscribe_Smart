"""Тексты экранов и форматирование для inline-UI."""

from __future__ import annotations

from core.models import MediaTask
from services import db
from utils.telegram_format import bold_html, escape_html, link_html, storage_link_line

TELEGRAM_PREVIEW_LIMIT = 3800


def media_header(
    task: MediaTask,
    *,
    icon: str = "🎬",
    extra_lines: list[str] | None = None,
) -> str:
    """Единый заголовок карточки видео для всех экранов."""
    lines = [
        f"{icon} {bold_html(task.title)}",
        f"📺 {escape_html(task.channel)} • ⏱ {escape_html(task.duration_formatted)}",
    ]
    if task.video_id:
        lines.append(
            link_html("YouTube", f"https://www.youtube.com/watch?v={task.video_id}")
        )
    if extra_lines:
        lines.extend(extra_lines)
    return "\n".join(lines)


def error_text(stage: str, message: str, technical: str = "") -> str:
    lines = [f"❌ {bold_html(stage)}", escape_html(message)]
    if technical:
        lines.append(f"<i>Подробнее: {escape_html(technical[:300])}</i>")
    return "\n".join(lines)


def download_done_text(
    task: MediaTask,
    *,
    format_type: str,
    size_mb: float,
    gdrive_url: str | None = None,
    gdrive_error: str = "",
    can_send: bool = False,
    file_limit_mb: float = 50.0,
    enable_gdrive: bool = True,
) -> str:
    lines = [
        media_header(task, icon="✅"),
        "",
        f"📦 {format_type.upper()} • {size_mb:.1f} MB",
    ]
    if gdrive_url:
        lines.append(storage_link_line("Открыть на Google Drive", gdrive_url))
    elif enable_gdrive:
        detail = f": {escape_html(gdrive_error[:100])}" if gdrive_error else ""
        lines.append(f"⚠️ GDrive: не загружен{detail}")
    if can_send:
        lines.append("\n📥 Файл можно получить в чат.")
    else:
        lines.append(f"\n⚠️ Файл > {file_limit_mb:.0f} MB — в чат не отправить.")
    lines.append("\nВыберите действие:")
    return "\n".join(lines)


def truncate_plain(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[: limit - 40].rstrip() + "\n\n(…продолжение на GDrive)", True


def video_card_text(
    task: MediaTask,
    *,
    has_transcript: bool = False,
    from_cache: bool = False,
) -> str:
    notes: list[str] = []
    if from_cache:
        notes.append("Метаданные из базы")
    if has_transcript:
        notes.append("Транскрипт уже есть в истории")
    extra = [f"<i>{escape_html(n)}</i>" for n in notes] if notes else None
    return media_header(task, extra_lines=extra) + "\n\nВыберите действие:"


def transcript_done_text(
    task: MediaTask,
    text: str,
    *,
    cached: bool = False,
    show_preview: bool = False,
) -> str:
    state = db.get_state(task.video_id)
    gdrive_md_url = state.gdrive_transcript_url if state else ""
    lines = [
        media_header(task, icon="✅"),
        f"📄 Транскрипт: {len(text)} символов" + (" <i>(кэш)</i>" if cached else ""),
    ]
    if show_preview and text.strip():
        preview, truncated = truncate_plain(text.strip(), 1200)
        lines.append("")
        lines.append(f"<pre>{escape_html(preview)}</pre>")
        if truncated:
            lines.append("<i>…полный текст — «📤 Экспорт» или GDrive</i>")
    if gdrive_md_url:
        lines.append(storage_link_line("Файл транскрипта", gdrive_md_url))
    elif not cached:
        lines.append("⚠️ GDrive: не загружен")
    lines.append("\nВыберите действие:")
    return "\n".join(lines)


    lines.append("\nВыберите действие:")
    return "\n".join(lines)


def home_text(user_name: str = "друг") -> str:
    return (
        f"👋 {bold_html('YTS Bot')}\n"
        f"Привет, {escape_html(user_name)}!\n\n"
        "Вставьте ссылку YouTube в чат или выберите раздел ниже.\n"
        "<i>Все кнопки обновляют это сообщение — без лишних пузырей.</i>"
    )


def new_link_prompt_text() -> str:
    return (
        f"📥 {bold_html('Новая ссылка')}\n\n"
        "Отправьте URL YouTube одним сообщением в чат.\n"
        "<i>Это сообщение можно удалить — карточка видео появится здесь.</i>"
    )


def help_text() -> str:
    from core.config import features

    lines = [f"{bold_html('YTS Bot')} — транскрибация и AI-анализ YouTube.\n"]
    if features.downloader:
        lines.append("• Ссылка → скачать M4A/MP4")
    if features.transcript:
        lines.append("• Транскрибация (Groq Whisper)")
        lines.append("• Пакет: .txt / .zip / несколько URL")
    if features.llm:
        lines.append("• Авто-саммари после транскрибации")
        lines.append("• 🧠 AI — доп. анализ и 💬 диалог по теме")
    lines.append("\n📜 История — база транскриптов")
    lines.append("☁️ GDrive — sync JSON на Drive")
    lines.append("🧹 Очистить — удалить сообщения бота из чата")
    return "\n".join(lines)


def analysis_menu_text(
    task: MediaTask,
    *,
    past_shown: int = 0,
    past_total: int = 0,
) -> str:
    header = media_header(task, icon="🎯") + "\n\n"
    if past_total:
        header += f"📄 Прошлые: {past_shown} из {past_total}\n\n"
    header += "Выберите прошлый анализ или новый тип:"
    return header


def llm_result_text(
    task: MediaTask,
    label: str,
    result: str,
    *,
    transcript_len: int,
    gdrive_url: str | None = None,
    show_body: bool = True,
) -> str:
    header = f"🧠 {bold_html(label)}\n<i>{escape_html(task.title)}</i>\n\n"
    lines: list[str]
    if show_body:
        body, truncated = truncate_plain(result, TELEGRAM_PREVIEW_LIMIT - len(header) - 120)
        lines = [header + escape_html(body), "", f"📝 {transcript_len} симв. • 🧠 {len(result)} симв."]
        if truncated:
            lines.append("<i>…полный текст — «📤 Экспорт» или GDrive</i>")
    else:
        lines = [
            f"✅ {bold_html(label)} — готово",
            f"<i>{escape_html(task.title)}</i>",
            "",
            f"📝 {transcript_len} симв. • 🧠 {len(result)} симв.",
            "<i>Текст ответа скрыт (настройки). GDrive или «📤 Экспорт».</i>",
        ]
    if gdrive_url:
        lines.append(storage_link_line("Полный документ", gdrive_url))
    return "\n".join(lines)


def ai_chat_prompt_text(label: str, task: MediaTask, *, turns: int = 0) -> str:
    header = media_header(task, icon="💬")
    lines = [
        header,
        "",
        f"Тема: {bold_html(label)}",
        f"Диалог: {turns} вопрос(ов)" if turns else "Задайте вопрос по этому анализу.",
        "",
        "Напишите сообщение одним ответом.",
        "<i>Контекст: retrieval — фрагменты транскрипта + анализ.</i>",
        "<i>/cancel — отмена</i>",
    ]
    return "\n".join(lines)


def ai_chat_reply_text(
    label: str,
    task: MediaTask,
    *,
    question: str,
    answer: str,
    turns: int,
    gdrive_url: str | None = None,
) -> str:
    q_preview, _ = truncate_plain(question.strip(), 400)
    a_preview, truncated = truncate_plain(answer.strip(), TELEGRAM_PREVIEW_LIMIT - 500)
    lines = [
        f"💬 {bold_html(label)}",
        f"<i>{escape_html(task.title)}</i>",
        "",
        f"<b>Вы:</b> {escape_html(q_preview)}",
        "",
        f"<b>AI:</b> {escape_html(a_preview)}",
        "",
        f"Диалог: {turns} • 🧠 {len(answer)} симв.",
    ]
    if truncated:
        lines.append("<i>…«📤 Экспорт» или GDrive для полного JSON</i>")
    if gdrive_url:
        lines.append(storage_link_line("JSON на Drive", gdrive_url))
    lines.append("\n<i>Можно задать следующий вопрос.</i>")
    return "\n".join(lines)


def view_result_text(
    label: str,
    result: str,
    *,
    created_at: str = "",
    show_body: bool = True,
) -> str:
    if not show_body:
        header = f"✅ {bold_html(label)}"
        if created_at:
            header += f"\n<i>{escape_html(created_at[:16])}</i>"
        return (
            f"{header}\n\n"
            f"🧠 {len(result)} симв.\n"
            f"<i>Текст скрыт. «📤 AI в чат» или GDrive.</i>"
        )
    header = f"📄 {bold_html(label)}"
    if created_at:
        header += f"\n<i>{escape_html(created_at[:16])}</i>"
    header += "\n\n"
    body, truncated = truncate_plain(result, TELEGRAM_PREVIEW_LIMIT - len(header))
    text = header + escape_html(body)
    if truncated:
        text += "\n\n<i>Открыть полный текст: GDrive или «📤 AI в чат».</i>"
    return text


def settings_text(
    settings,
    *,
    global_transcript: bool,
    global_llm: bool,
    default_whisper: str,
    default_llm: str,
) -> str:
    from services import user_settings as us

    on = "✅ вкл"
    off = "❌ выкл"
    w_mark = " *" if settings.whisper_custom else ""
    l_mark = " *" if settings.llm_custom else ""
    lang_mark = " *" if settings.language_custom else ""

    return (
        f"{bold_html('Настройки')}\n\n"
        f"<b>Вывод в чат</b>\n"
        f"📝 Транскрипт: {on if settings.show_transcript_in_chat else off}\n"
        f"🧠 AI-ответ: {on if settings.show_llm_in_chat else off}\n\n"
        f"<b>Обработка</b>\n"
        f"🎙 Whisper: {escape_html(us.whisper_label(settings.whisper_model))}{w_mark}\n"
        f"🧠 LLM: {escape_html(us.llm_label(settings.llm_model))}{l_mark}\n"
        f"🌐 Язык STT: {escape_html(us.language_label(settings.transcribe_language))}{lang_mark}\n\n"
        f"<i>Defaults (.env): транскрипт={'on' if global_transcript else 'off'}, "
        f"AI={'on' if global_llm else 'off'}, "
        f"whisper={escape_html(default_whisper)}, llm={escape_html(default_llm)}</i>\n"
        f"<i>* — ваш override</i>"
    )

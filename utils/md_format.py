"""
LEGACY: каноническая структура .md (до JSON v1.0).

Используется только для fallback-чтения и migrate. Новые транскрипты — JSON.

Иерархия заголовков (исторически):
  #   — название видео
  ##  — «Транскрипт» и «AI-анализ»
  ### — тема AI
"""

from __future__ import annotations

import re
from datetime import datetime

from core.models import MediaTask

MARKER_TRANSCRIPT = "## Транскрипт"
MARKER_AI = "## AI-анализ"
ALLOWED_H2 = frozenset({MARKER_TRANSCRIPT, MARKER_AI})

# legacy
MARKER_METADATA = "## Метаданные"
LEGACY_AI_RE = re.compile(r"^## AI:", re.MULTILINE)
AI_TOPIC_RE = re.compile(r"^### .+", re.MULTILINE)
PROCESSED_AT_RE = re.compile(r"- \*\*Дата обработки:\*\* (.+)", re.MULTILINE)
HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.+)$")


def sanitize_ai_body(text: str) -> str:
    """
    Приводит тело ответа LLM к уровню ####.
    # / ## / ### из ответа модели не должны ломать парсер документа.
    """
    out: list[str] = []
    for line in text.splitlines():
        m = HEADING_LINE_RE.match(line)
        if m and len(m.group(1)) <= 6:
            out.append(f"#### {m.group(2).strip()}")
        else:
            out.append(line)
    return "\n".join(out).strip()


def ai_section_count(content: str) -> int:
    if MARKER_AI in content:
        tail = content.split(MARKER_AI, 1)[1]
        return len(AI_TOPIC_RE.findall(tail))
    return len(LEGACY_AI_RE.findall(content))


def split_base_and_ai(content: str) -> tuple[str, str]:
    for marker in (f"\n---\n\n{MARKER_AI}", f"\n{MARKER_AI}", "\n---\n\n## AI:"):
        idx = content.find(marker)
        if idx != -1:
            return content[:idx].rstrip(), content[idx:].lstrip()
    return content.rstrip(), ""


def strip_trailing_separators(base: str) -> str:
    base = base.rstrip()
    while base.endswith("\n---") or base.endswith("---"):
        base = base.removesuffix("\n---").removesuffix("---").rstrip()
    return base


def extract_transcript_body(content: str) -> str:
    base, _ = split_base_and_ai(content)
    base = strip_trailing_separators(base)

    idx = base.find(MARKER_TRANSCRIPT)
    if idx == -1:
        return base.strip()

    body = base[idx + len(MARKER_TRANSCRIPT):].lstrip("\n")
    for marker in (
        f"\n---\n\n{MARKER_AI}",
        f"\n{MARKER_AI}",
        "\n---\n\n## AI:",
        f"\n{MARKER_METADATA}",
        "\n## Главы",
        "\n## Описание",
    ):
        pos = body.find(marker)
        if pos != -1:
            body = body[:pos]
    sep = body.find("\n---\n")
    if sep != -1:
        body = body[:sep]
    return body.strip()


def extract_processed_at(content: str) -> str | None:
    match = PROCESSED_AT_RE.search(content)
    return match.group(1).strip() if match else None


def _fmt_int(value: int | None) -> str:
    if value is None:
        return "—"
    return f"{value:,}".replace(",", " ")


def render_base_document(
    task: MediaTask,
    transcript: str,
    *,
    processed_at: str | None = None,
) -> str:
    """# заголовок → метаданные (bullets) → ## Транскрипт."""
    ts = processed_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = [
        f"# {task.title.strip()}",
        "",
        f"- **Канал:** {task.channel or '—'}",
        f"- **Video ID:** {task.video_id or '—'}",
        f"- **URL:** {task.url or '—'}",
        f"- **Дата публикации:** {task.upload_date_formatted}",
        f"- **Длительность:** {task.duration_formatted}",
        f"- **Язык:** {task.language or '—'}",
        f"- **Просмотры:** {_fmt_int(task.view_count)}",
        f"- **Лайки:** {_fmt_int(task.like_count)}",
        f"- **Комментарии:** {_fmt_int(task.comment_count)}",
        f"- **Дата обработки:** {ts}",
        "",
        "---",
        "",
        MARKER_TRANSCRIPT,
        "",
        transcript.strip(),
        "",
    ]
    return "\n".join(lines)


def format_ai_entry(label: str, prompt: str, result: str, *, created_at: str | None = None) -> str:
    ts = created_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    body = sanitize_ai_body(result)
    lines = [
        f"### {label.strip()}",
        f"- **Дата:** {ts}",
        f"- **Промпт:** {prompt.strip()}",
        "",
        body,
        "",
    ]
    return "\n".join(lines)


def format_ai_block(entries: list[dict]) -> str:
    if not entries:
        return ""
    parts = [MARKER_AI, ""]
    for entry in entries:
        parts.append(
            format_ai_entry(
                entry["label"],
                entry["prompt"],
                entry["result"],
                created_at=entry.get("created_at"),
            )
        )
    return "\n".join(parts).rstrip() + "\n"


def render_document(
    task: MediaTask,
    transcript: str,
    ai_entries: list[dict] | None = None,
    *,
    processed_at: str | None = None,
) -> str:
    base = render_base_document(task, transcript, processed_at=processed_at).rstrip()
    ai_block = format_ai_block(ai_entries or [])
    if not ai_block:
        return base + "\n"
    return base + f"\n\n---\n\n" + ai_block


def append_ai_to_content(content: str, label: str, prompt: str, result: str) -> str:
    base, _ = split_base_and_ai(content)
    base = strip_trailing_separators(base)
    entry = format_ai_entry(label, prompt, result)
    if MARKER_AI in content:
        return base.rstrip() + "\n\n" + entry
    return base + f"\n\n---\n\n{MARKER_AI}\n\n" + entry


def rebuild_content_with_ai(base_content: str, entries: list[dict]) -> str:
    base, _ = split_base_and_ai(base_content)
    base = strip_trailing_separators(base)
    ai_block = format_ai_block(entries)
    if not ai_block:
        return base + "\n"
    return base + f"\n\n---\n\n" + ai_block


def _count_h1(content: str) -> int:
    return sum(
        1 for line in content.splitlines()
        if line.startswith("# ") and not line.startswith("##")
    )


def _iter_h2(content: str) -> list[str]:
    return [
        m.group(1).strip()
        for m in re.finditer(r"^## (.+)$", content, re.MULTILINE)
    ]


def validate_document(content: str) -> list[str]:
    issues: list[str] = []
    if not content.strip():
        return ["пустой файл"]

    if _count_h1(content) != 1:
        issues.append("ровно один заголовок # (название видео)")

    if MARKER_TRANSCRIPT not in content:
        issues.append("нет секции «## Транскрипт»")

    for legacy in (MARKER_METADATA, "## Главы", "## Описание видео", "## AI:"):
        if legacy in content:
            issues.append(f"legacy-секция: {legacy}")

    h2_titles = _iter_h2(content)
    for title in h2_titles:
        section = f"## {title}"
        if section not in ALLOWED_H2:
            issues.append(f"лишний ## «{title}» (допустимы только Транскрипт и AI-анализ)")

    if MARKER_AI in content:
        ai_part = content.split(MARKER_AI, 1)[1]
        for line in ai_part.splitlines():
            if re.match(r"^# ", line) or re.match(r"^## ", line):
                issues.append("в AI-блоке запрещены # и ## (используйте ####)")
                break

    base, _ = split_base_and_ai(content)
    if MARKER_AI in base:
        issues.append("«AI-анализ» внутри базовой части")

    if MARKER_AI in content:
        before = content[: content.find(MARKER_AI)].rstrip()
        if not before.endswith("---"):
            issues.append("нет --- перед «## AI-анализ»")

    if LEGACY_AI_RE.search(content) and MARKER_AI not in content:
        issues.append("legacy-формат AI (## AI:)")

    if not extract_transcript_body(content):
        issues.append("пустой транскрипт")

    return issues

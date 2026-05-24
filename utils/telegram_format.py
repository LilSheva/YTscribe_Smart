"""Безопасное форматирование текстов для Telegram."""

from __future__ import annotations

import html
import re

# Legacy Markdown (parse_mode=Markdown)
_MD_SPECIAL = re.compile(r"([_*\[`\\])")


def escape_markdown(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", text)


def bold_md(text: str) -> str:
    return f"*{escape_markdown(text)}*"


def italic_md(text: str) -> str:
    return f"_{escape_markdown(text)}_"


def link_html(label: str, url: str) -> str:
    return f'<a href="{escape_html(url)}">{escape_html(label)}</a>'


def storage_link_line(label: str, url_or_path: str) -> str:
    """Ссылка HTTP или локальный путь (режим Google Drive Desktop)."""
    if url_or_path.startswith("http"):
        return link_html(label, url_or_path)
    return f"📁 {escape_html(label)}: <code>{escape_html(url_or_path)}</code>"


def escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def bold_html(text: str) -> str:
    return f"<b>{escape_html(text)}</b>"

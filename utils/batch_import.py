"""Извлечение текста из файлов пакетного импорта (.txt, экспорт Telegram .zip)."""

from __future__ import annotations

import logging
import zipfile
from io import BytesIO

logger = logging.getLogger(__name__)

_TEXT_SUFFIXES = (".txt", ".html", ".htm", ".json", ".md")
_ZIP_MIME_PARTS = ("zip", "compressed")


def is_zip_document(*, file_name: str | None, mime_type: str | None) -> bool:
    name = (file_name or "").lower()
    mime = (mime_type or "").lower()
    return name.endswith(".zip") or any(part in mime for part in _ZIP_MIME_PARTS)


def is_text_document(*, file_name: str | None, mime_type: str | None) -> bool:
    name = (file_name or "").lower()
    mime = (mime_type or "").lower()
    if any(name.endswith(suffix) for suffix in _TEXT_SUFFIXES):
        return True
    return mime.startswith("text/") or mime in ("application/json", "application/xml")


def decode_bytes(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def extract_text_from_zip(raw: bytes) -> str:
    """Читает messages.html / .txt / .json из экспорта Telegram."""
    parts: list[str] = []
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            lower = name.lower()
            if not lower.endswith(_TEXT_SUFFIXES):
                continue
            try:
                parts.append(decode_bytes(zf.read(name)))
            except Exception as e:
                logger.warning("batch_import: skip %s: %s", name, e)
    return "\n".join(parts)


def load_import_text(raw: bytes, *, file_name: str | None, mime_type: str | None) -> str | None:
    """Возвращает текст для парсинга URL или None, если формат не поддержан."""
    if is_zip_document(file_name=file_name, mime_type=mime_type):
        text = extract_text_from_zip(raw)
        return text or None
    if is_text_document(file_name=file_name, mime_type=mime_type):
        return decode_bytes(raw)
    return None

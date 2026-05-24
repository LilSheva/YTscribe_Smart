"""Per-user настройки: вывод в чат, модели Whisper/LLM, язык транскрибации."""

from __future__ import annotations

from dataclasses import dataclass

from core.config import LLM_MODEL, SHOW_LLM_IN_CHAT, SHOW_TRANSCRIPT_IN_CHAT, WHISPER_MODEL
from services import db
from services import llm_router
from services.transcriber import get_available_models

TRANSCRIBE_LANGUAGES: dict[str, str] = {
    "auto": "Авто",
    "ru": "Русский",
    "en": "English",
}
LANGUAGE_ORDER = ("auto", "ru", "en")


@dataclass(frozen=True)
class ChatOutputSettings:
    show_transcript_in_chat: bool
    show_llm_in_chat: bool


@dataclass(frozen=True)
class UserSettings:
    show_transcript_in_chat: bool
    show_llm_in_chat: bool
    whisper_model: str
    llm_model: str
    transcribe_language: str
    whisper_custom: bool = False
    llm_custom: bool = False
    language_custom: bool = False


def get_user_settings(user_id: int) -> UserSettings:
    row = db.get_user_settings_row(user_id)
    whisper = WHISPER_MODEL
    llm = LLM_MODEL
    language = "auto"
    transcript = SHOW_TRANSCRIPT_IN_CHAT
    show_llm = SHOW_LLM_IN_CHAT
    whisper_custom = llm_custom = language_custom = False

    if row:
        if row.show_transcript_in_chat is not None:
            transcript = row.show_transcript_in_chat
        if row.show_llm_in_chat is not None:
            show_llm = row.show_llm_in_chat
        if row.whisper_model:
            whisper = row.whisper_model
            whisper_custom = whisper != WHISPER_MODEL
        if row.llm_model:
            llm = row.llm_model
            llm_custom = llm != LLM_MODEL
        if row.transcribe_language:
            language = row.transcribe_language
            language_custom = language != "auto"

    if language not in TRANSCRIBE_LANGUAGES:
        language = "auto"

    if whisper not in get_available_models():
        whisper_custom = True
    if llm not in llm_router.get_available_models():
        llm_custom = True

    return UserSettings(
        show_transcript_in_chat=transcript,
        show_llm_in_chat=show_llm,
        whisper_model=whisper,
        llm_model=llm,
        transcribe_language=language,
        whisper_custom=whisper_custom,
        llm_custom=llm_custom,
        language_custom=language_custom,
    )


def get_chat_output_settings(user_id: int) -> ChatOutputSettings:
    s = get_user_settings(user_id)
    return ChatOutputSettings(
        show_transcript_in_chat=s.show_transcript_in_chat,
        show_llm_in_chat=s.show_llm_in_chat,
    )


def toggle_transcript_in_chat(user_id: int) -> UserSettings:
    current = get_user_settings(user_id)
    db.upsert_user_settings(user_id, show_transcript_in_chat=not current.show_transcript_in_chat)
    return get_user_settings(user_id)


def toggle_llm_in_chat(user_id: int) -> UserSettings:
    current = get_user_settings(user_id)
    db.upsert_user_settings(user_id, show_llm_in_chat=not current.show_llm_in_chat)
    return get_user_settings(user_id)


def set_whisper_model(user_id: int, model_id: str) -> UserSettings:
    db.upsert_user_settings(user_id, whisper_model=model_id)
    return get_user_settings(user_id)


def set_llm_model(user_id: int, model_id: str) -> UserSettings:
    db.upsert_user_settings(user_id, llm_model=model_id)
    return get_user_settings(user_id)


def cycle_transcribe_language(user_id: int) -> UserSettings:
    current = get_user_settings(user_id)
    try:
        idx = LANGUAGE_ORDER.index(current.transcribe_language)
    except ValueError:
        idx = 0
    nxt = LANGUAGE_ORDER[(idx + 1) % len(LANGUAGE_ORDER)]
    db.upsert_user_settings(user_id, transcribe_language=nxt)
    return get_user_settings(user_id)


def restore_user_settings(user_id: int, snapshot: UserSettings) -> UserSettings:
    """Откат per-user настроек к сохранённому снимку."""
    db.upsert_user_settings(
        user_id,
        show_transcript_in_chat=snapshot.show_transcript_in_chat,
        show_llm_in_chat=snapshot.show_llm_in_chat,
        whisper_model=snapshot.whisper_model,
        llm_model=snapshot.llm_model,
        transcribe_language=snapshot.transcribe_language,
    )
    return get_user_settings(user_id)


def reset_to_defaults(user_id: int) -> UserSettings:
    db.upsert_user_settings(user_id, clear_all=True)
    return get_user_settings(user_id)


def whisper_label(model_id: str) -> str:
    return get_available_models().get(model_id, model_id)


def llm_label(model_id: str) -> str:
    return llm_router.get_available_models().get(model_id, model_id)


def language_label(code: str) -> str:
    return TRANSCRIBE_LANGUAGES.get(code, code)

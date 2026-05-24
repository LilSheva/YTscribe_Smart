"""Helpers для экрана настроек."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from bot.keyboards.main_menu import (
    get_llm_picker_keyboard,
    get_settings_keyboard,
    get_whisper_picker_keyboard,
)
from bot.ui import screens
from core.config import LLM_MODEL, SHOW_LLM_IN_CHAT, SHOW_TRANSCRIPT_IN_CHAT, WHISPER_MODEL
from services import user_settings


def render_settings_message(settings: user_settings.UserSettings) -> tuple[str, InlineKeyboardMarkup]:
    text = screens.settings_text(
        settings,
        global_transcript=SHOW_TRANSCRIPT_IN_CHAT,
        global_llm=SHOW_LLM_IN_CHAT,
        default_whisper=WHISPER_MODEL,
        default_llm=LLM_MODEL,
    )
    return text, get_settings_keyboard()


def render_whisper_picker(settings: user_settings.UserSettings) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "<b>Модель Whisper</b>\n\n"
        "Текущая: "
        f"<code>{settings.whisper_model}</code>\n\n"
        "Выберите модель для транскрибации:"
    )
    return text, get_whisper_picker_keyboard(settings.whisper_model)


def render_llm_picker(settings: user_settings.UserSettings) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "<b>Модель LLM</b>\n\n"
        f"Текущая: <code>{settings.llm_model}</code>\n\n"
        "Выберите модель для AI-анализа:"
    )
    return text, get_llm_picker_keyboard(settings.llm_model)

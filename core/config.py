"""
core/config.py — Конфигурация проекта YTS_bot.

Считывает переменные из .env файла и предоставляет их
как типизированные атрибуты модуля. Включает Feature Toggles.
"""

import os
import logging
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

# Корень проекта (на уровень выше от core/)
BASE_DIR: Path = Path(__file__).resolve().parent.parent

# Загрузка .env из корня проекта
load_dotenv(BASE_DIR / ".env")

logger = logging.getLogger(__name__)


def _get_env(key: str, default: str = "") -> str:
    """Получить переменную окружения с fallback."""
    value = os.getenv(key, default)
    if not value and not default:
        logger.warning(f"Переменная окружения '{key}' не задана!")
    return value


def _get_bool(key: str, default: bool = False) -> bool:
    """Получить булевый флаг из окружения."""
    raw = os.getenv(key, str(default)).strip().lower()
    return raw in ("true", "1", "yes")


# === Telegram ===
BOT_TOKEN: str = _get_env("BOT_TOKEN")

# === API Keys ===
GROQ_API_KEY: str = _get_env("GROQ_API_KEY")
OMNIROUTE_API_KEY: str = _get_env("OMNIROUTE_API_KEY")

# === Google Drive ===
GDRIVE_CREDENTIALS_PATH: str = _get_env("GDRIVE_CREDENTIALS_PATH", "credentials/gdrive_service.json")

# === Media ===
BROWSER_FOR_COOKIES: str = _get_env("BROWSER_FOR_COOKIES", "chrome")
TEMP_DIR: Path = BASE_DIR / _get_env("TEMP_DIR", "temp")

# === Feature Toggles ===
ENABLE_DOWNLOADER: bool = _get_bool("ENABLE_DOWNLOADER", default=True)
ENABLE_TRANSCRIPT: bool = _get_bool("ENABLE_TRANSCRIPT", default=True)
ENABLE_LLM: bool = _get_bool("ENABLE_LLM", default=True)
ENABLE_DB: bool = _get_bool("ENABLE_DB", default=True)
ENABLE_GDRIVE: bool = _get_bool("ENABLE_GDRIVE", default=False)

# === LLM Settings ===
LLM_MODEL: str = _get_env("LLM_MODEL", "claude-3-5-sonnet-20241022")
LLM_MAX_TOKENS: int = int(_get_env("LLM_MAX_TOKENS", "4096"))

# === Logging ===
LOG_LEVEL: str = _get_env("LOG_LEVEL", "INFO").upper()


@dataclass(frozen=True)
class FeatureConfig:
    """Сводная информация о статусе модулей (для диагностики)."""

    downloader: bool = ENABLE_DOWNLOADER
    transcript: bool = ENABLE_TRANSCRIPT
    llm: bool = ENABLE_LLM
    db: bool = ENABLE_DB
    gdrive: bool = ENABLE_GDRIVE

    def summary(self) -> str:
        """Человекочитаемый статус всех модулей."""
        flags = {
            "DOWNLOADER": self.downloader,
            "TRANSCRIPT": self.transcript,
            "LLM": self.llm,
            "DB (ChromaDB)": self.db,
            "GDRIVE": self.gdrive,
        }
        lines = [f"  {'✅' if v else '❌'} {k}" for k, v in flags.items()]
        return "Feature Toggles:\n" + "\n".join(lines)


# Синглтон для быстрого доступа
features = FeatureConfig()

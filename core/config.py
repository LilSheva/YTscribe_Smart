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
ALLOWED_USER_IDS: set[int] = {
    int(uid.strip())
    for uid in _get_env("ALLOWED_USER_IDS", "").split(",")
    if uid.strip().isdigit()
}

# === API Keys ===
GROQ_API_KEY: str = _get_env("GROQ_API_KEY")
OMNIROUTE_API_KEY: str = _get_env("OMNIROUTE_API_KEY")
OMNIROUTE_BASE_URL: str = _get_env("OMNIROUTE_BASE_URL", "https://openrouter.ai/api/v1")

# === Транскрибация ===
TRANSCRIPTION_PROVIDER: str = _get_env("TRANSCRIPTION_PROVIDER", "groq")
WHISPER_MODEL: str = _get_env("WHISPER_MODEL", "whisper-large-v3-turbo")
WHISPER_MAX_FILE_MB: int = int(_get_env("WHISPER_MAX_FILE_MB", "25"))

# === Google Drive ===
GDRIVE_CREDENTIALS_PATH: str = _get_env("GDRIVE_CREDENTIALS_PATH", "credentials/gdrive_service.json")
GDRIVE_MEDIA_FOLDER_ID: str = _get_env("GDRIVE_MEDIA_FOLDER_ID")
GDRIVE_TRANSCRIPTS_FOLDER_ID: str = _get_env("GDRIVE_TRANSCRIPTS_FOLDER_ID")
# local = папка клиента Google Drive на ПК (синхронизация приложением); api = OAuth + Drive API
GDRIVE_LOCAL_DIR_RAW: str = _get_env("GDRIVE_LOCAL_DIR", "")
GDRIVE_LOCAL_DIR: Path | None = (
    Path(GDRIVE_LOCAL_DIR_RAW).expanduser() if GDRIVE_LOCAL_DIR_RAW.strip() else None
)
GDRIVE_MODE: str = _get_env(
    "GDRIVE_MODE",
    "local" if GDRIVE_LOCAL_DIR else "api",
).strip().lower()

# === Media / Cookies ===
TEMP_DIR: Path = BASE_DIR / _get_env("TEMP_DIR", "temp")

# Порядок браузеров для fallback-цепочки (Firefox первым — нет DPAPI)
COOKIE_BROWSERS: list[str] = [
    b.strip()
    for b in _get_env("COOKIE_BROWSERS", "firefox,chrome,zen").split(",")
    if b.strip()
]
# Максимальный возраст cookies.txt при котором он считается свежим
COOKIE_MAX_AGE_HOURS: float = float(_get_env("COOKIE_MAX_AGE_HOURS", "6"))
# Интервал фонового обновления cookies.txt
COOKIE_REFRESH_INTERVAL_HOURS: float = float(_get_env("COOKIE_REFRESH_INTERVAL_HOURS", "4"))

# === Feature Toggles ===
ENABLE_DOWNLOADER: bool = _get_bool("ENABLE_DOWNLOADER", default=True)
ENABLE_TRANSCRIPT: bool = _get_bool("ENABLE_TRANSCRIPT", default=True)
ENABLE_LLM: bool = _get_bool("ENABLE_LLM", default=True)
ENABLE_DB: bool = _get_bool("ENABLE_DB", default=False)
ENABLE_KB: bool = _get_bool("ENABLE_KB", default=False)
ENABLE_GDRIVE: bool = _get_bool("ENABLE_GDRIVE", default=True)
ENABLE_HISTORY: bool = _get_bool("ENABLE_HISTORY", default=True)
DATA_DIR: Path = BASE_DIR / "data"

# === Predictive progress (timing stats) ===
TIMING_MIN_SAMPLES: int = max(1, int(_get_env("TIMING_MIN_SAMPLES", "5")))
TIMING_RECENT_K: int = max(1, int(_get_env("TIMING_RECENT_K", "10")))

# === GDrive sync ===
GDRIVE_SYNC_ON_START: bool = _get_bool("GDRIVE_SYNC_ON_START", default=False)
GDRIVE_SYNC_AUTO_REPAIR: bool = _get_bool("GDRIVE_SYNC_AUTO_REPAIR", default=False)

# === Chat output toggles (global defaults; per-user overrides in SQLite) ===
SHOW_TRANSCRIPT_IN_CHAT: bool = _get_bool("SHOW_TRANSCRIPT_IN_CHAT", default=False)
SHOW_LLM_IN_CHAT: bool = _get_bool("SHOW_LLM_IN_CHAT", default=True)

# === Knowledge Base (внешний сервис) ===
KB_API_URL: str = _get_env("KB_API_URL", "") if ENABLE_KB else os.getenv("KB_API_URL", "")

# === LLM Settings ===
LLM_MODEL: str = _get_env("LLM_MODEL", "claude-3-5-sonnet-20241022")
LLM_MAX_TOKENS: int = int(_get_env("LLM_MAX_TOKENS", "4096"))
AUTO_LLM_SUMMARY: bool = _get_bool("AUTO_LLM_SUMMARY", default=True)
LLM_CACHE_ENABLED: bool = _get_bool("LLM_CACHE_ENABLED", default=True)
LLM_CACHE_MAX_ENTRIES: int = max(50, int(_get_env("LLM_CACHE_MAX_ENTRIES", "500")))

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
            "KB (внешняя)": self.db,
            "GDRIVE": self.gdrive,
        }
        lines = [f"  {'✅' if v else '❌'} {k}" for k, v in flags.items()]
        return "Feature Toggles:\n" + "\n".join(lines)


# Синглтон для быстрого доступа
features = FeatureConfig()

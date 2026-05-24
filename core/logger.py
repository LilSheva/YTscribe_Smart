"""
core/logger.py — Настройка логгирования для YTS_bot.

Единая точка конфигурации логгера. Все модули используют:
    logger = logging.getLogger(__name__)
"""

import io
import logging
import os
import sys
from pathlib import Path


def is_headless() -> bool:
    """True если процесс без консоли (pythonw / YTS_HEADLESS=1)."""
    flag = os.environ.get("YTS_HEADLESS", "").strip().lower()
    if flag in ("1", "true", "yes"):
        return True
    return Path(sys.executable).name.lower() == "pythonw.exe"


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    """
    Инициализирует корневой логгер.

    Args:
        level: Уровень логгирования (DEBUG, INFO, WARNING, ERROR).
        log_dir: Директория для файла логов (опционально).
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    headless = is_headless()

    formatter = logging.Formatter(
        fmt="[%(asctime)s] %(levelname)-8s | %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    root_logger.handlers.clear()

    if not headless and sys.stdout is not None and hasattr(sys.stdout, "buffer"):
        utf8_stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        console_handler = logging.StreamHandler(utf8_stdout)
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            log_dir / "yts_bot.log", encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    elif headless:
        raise RuntimeError("Headless mode requires log_dir for file logging")

    logging.getLogger(__name__).debug(
        f"Логгирование инициализировано (level={level}, headless={headless})"
    )

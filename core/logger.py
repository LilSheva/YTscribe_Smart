"""
core/logger.py — Настройка логгирования для YTS_bot.

Единая точка конфигурации логгера. Все модули используют:
    logger = logging.getLogger(__name__)
"""

import logging
import sys
from pathlib import Path


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    """
    Инициализирует корневой логгер.

    Args:
        level: Уровень логгирования (DEBUG, INFO, WARNING, ERROR).
        log_dir: Директория для файла логов (опционально).
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Формат: [время] УРОВЕНЬ | модуль — сообщение
    formatter = logging.Formatter(
        fmt="[%(asctime)s] %(levelname)-8s | %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # --- Console handler ---
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)

    # --- Корневой логгер ---
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Очистка предыдущих хэндлеров (при повторном вызове)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)

    # --- File handler (опционально) ---
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            log_dir / "yts_bot.log", encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)  # В файл пишем всё
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    logging.getLogger(__name__).debug(
        f"Логгирование инициализировано (level={level})"
    )

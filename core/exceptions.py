"""
core/exceptions.py — Кастомные исключения YTS_bot.

Иерархия ошибок для корректной обработки на уровне хэндлеров.
"""


class YTSBotError(Exception):
    """Базовое исключение проекта."""

    def __init__(self, message: str = "Внутренняя ошибка бота") -> None:
        self.message = message
        super().__init__(self.message)


class ServiceDisabledError(YTSBotError):
    """Модуль отключён через Feature Toggle."""

    def __init__(self, service_name: str) -> None:
        self.service_name = service_name
        super().__init__(f"Модуль '{service_name}' отключён в конфигурации.")


class DownloadError(YTSBotError):
    """Ошибка при скачивании медиа (yt-dlp)."""

    def __init__(self, url: str, detail: str = "") -> None:
        self.url = url
        msg = f"Ошибка загрузки: {url}"
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)


class TranscriptionError(YTSBotError):
    """Ошибка при транскрибации (Groq API)."""

    def __init__(self, detail: str = "API транскрибации временно недоступно") -> None:
        super().__init__(detail)


class LLMError(YTSBotError):
    """Ошибка при обращении к LLM (Omniroute)."""

    def __init__(self, detail: str = "LLM API временно недоступно") -> None:
        super().__init__(detail)

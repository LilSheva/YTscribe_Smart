"""
services/cookie_manager.py — Надёжное получение cookies YouTube для yt-dlp.

Fallback-цепочка при каждом вызове get_cookies_opts():
  1. Firefox  (нет DPAPI-шифрования — самый надёжный)
  2. Chrome   (не используется активно → профиль обычно не залочен)
  3. Zen      (не используется активно)
  4. cookies.txt (если файл свежий < COOKIE_MAX_AGE_HOURS)
  5. Edge     (основной браузер — профиль часто залочен, последний шанс)
  6. Без cookies (POT-токен может хватить)

Фоновое обновление: при старте и каждые COOKIE_REFRESH_INTERVAL_HOURS часов
тихо извлекает cookies из первого доступного браузера и сохраняет в cookies.txt.
Это гарантирует свежий файл как страховочный fallback.
"""

import asyncio
import logging
import time
from pathlib import Path

import yt_dlp
from yt_dlp.cookies import extract_cookies_from_browser, YoutubeDLCookieJar

from core.config import BASE_DIR, COOKIE_BROWSERS, COOKIE_MAX_AGE_HOURS, COOKIE_REFRESH_INTERVAL_HOURS

logger = logging.getLogger(__name__)

_COOKIES_FILE = BASE_DIR / "cookies.txt"

# Порядок браузеров для live-извлечения (без файла)
_BROWSER_CHAIN: list[str] = COOKIE_BROWSERS

# Время последнего успешного обновления файла (monotonic)
_last_refresh: float = 0.0
_refresh_lock = asyncio.Lock()

# Кеш: результат проверки браузера в рамках текущего процесса
# None = не проверяли, True/False = результат последней проверки
_browser_cache: dict[str, bool] = {}


def _cookies_file_age_hours() -> float:
    """Возвращает возраст cookies.txt в часах. 999 если файл не существует."""
    if not _COOKIES_FILE.exists():
        return 999.0
    age_sec = time.time() - _COOKIES_FILE.stat().st_mtime
    return age_sec / 3600


def _try_browser_opts(browser: str) -> dict | None:
    """
    Проверяет, доступен ли браузер для извлечения cookies.
    Результат кешируется на время процесса — браузер не перепроверяется при каждом запросе.
    """
    if browser in _browser_cache:
        return {"cookiesfrombrowser": (browser,)} if _browser_cache[browser] else None
    try:
        jar = extract_cookies_from_browser(browser)
        _browser_cache[browser] = jar is not None
        if _browser_cache[browser]:
            return {"cookiesfrombrowser": (browser,)}
    except Exception as e:
        logger.debug(f"Cookies: браузер '{browser}' недоступен — {e}")
        _browser_cache[browser] = False
    return None


def _export_cookies_to_file(browser: str) -> bool:
    """
    Экспортирует cookies из браузера в cookies.txt через yt-dlp.
    Возвращает True при успехе.
    """
    try:
        jar = extract_cookies_from_browser(browser)
        if jar is None:
            return False
        cookie_jar = YoutubeDLCookieJar(str(_COOKIES_FILE))
        for cookie in jar:
            cookie_jar.set_cookie(cookie)
        cookie_jar.save(ignore_discard=True, ignore_expires=True)
        if _COOKIES_FILE.exists() and _COOKIES_FILE.stat().st_size > 100:
            logger.info(f"Cookies: обновлён файл из браузера '{browser}' ({_COOKIES_FILE.stat().st_size} байт)")
            return True
    except Exception as e:
        logger.debug(f"Cookies: экспорт из '{browser}' не удался — {e}")
    return False


def refresh_cookies_file() -> bool:
    """
    Синхронно обновляет cookies.txt из первого доступного браузера.
    Вызывается из asyncio.to_thread().
    """
    for browser in _BROWSER_CHAIN:
        if _export_cookies_to_file(browser):
            return True
    logger.warning("Cookies: не удалось обновить файл ни из одного браузера")
    return False


async def maybe_refresh_cookies() -> None:
    """
    Асинхронно обновляет cookies.txt если прошло > COOKIE_REFRESH_INTERVAL_HOURS.
    Безопасно вызывать при каждом старте бота.
    """
    global _last_refresh
    async with _refresh_lock:
        elapsed = time.monotonic() - _last_refresh
        if elapsed < COOKIE_REFRESH_INTERVAL_HOURS * 3600 and _last_refresh > 0:
            return
        logger.info("Cookies: фоновое обновление cookies.txt...")
        success = await asyncio.to_thread(refresh_cookies_file)
        if success:
            _last_refresh = time.monotonic()


def get_cookies_opts() -> dict:
    """
    Возвращает словарь с настройками cookies для yt-dlp.

    Порядок:
      1. Живые браузеры из COOKIE_BROWSERS (Firefox первым)
      2. cookies.txt если файл достаточно свежий
      3. Edge как последний шанс среди браузеров
      4. Пустой словарь (без cookies)
    """
    # Шаги 1–3: пробуем браузеры по цепочке (Edge в конце если не в списке)
    full_chain = list(_BROWSER_CHAIN)
    if "edge" not in [b.lower() for b in full_chain]:
        full_chain.append("edge")

    for browser in full_chain:
        result = _try_browser_opts(browser)
        if result:
            logger.debug(f"Cookies: используем браузер '{browser}'")
            return {"cookiesfrombrowser": (browser,)}

    # Шаг 4: cookies.txt если свежий
    age = _cookies_file_age_hours()
    if age <= COOKIE_MAX_AGE_HOURS:
        logger.debug(f"Cookies: используем файл (возраст {age:.1f}ч)")
        return {"cookiefile": str(_COOKIES_FILE)}

    if _COOKIES_FILE.exists():
        logger.warning(f"Cookies: файл устарел ({age:.1f}ч > {COOKIE_MAX_AGE_HOURS}ч), но других вариантов нет — используем")
        return {"cookiefile": str(_COOKIES_FILE)}

    logger.warning("Cookies: не настроены — запрос без авторизации (только POT)")
    return {}

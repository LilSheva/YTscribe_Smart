"""Безопасные вызовы Telegram API (retry, fallback)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError

logger = logging.getLogger(__name__)


async def safe_edit_text(target: Any, text: str, *, retries: int = 2, **kwargs) -> bool:
    """
    edit_text с retry при сетевых сбоях и fallback без HTML.
    Возвращает True при успехе (в т.ч. «message is not modified»).
    """
    last_error: Exception | None = None
    kwargs_copy = dict(kwargs)

    for attempt in range(retries + 1):
        try:
            await target.edit_text(text, **kwargs_copy)
            return True
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "message is not modified" in err:
                return True
            if "parse" in err or "can't find" in err or "unsupported" in err:
                plain = dict(kwargs_copy)
                plain.pop("parse_mode", None)
                try:
                    await target.edit_text(text, parse_mode=None, **plain)
                    return True
                except TelegramBadRequest as e2:
                    if "message is not modified" in str(e2).lower():
                        return True
                    last_error = e2
                    break
            last_error = e
            break
        except TelegramNetworkError as e:
            last_error = e
            if attempt < retries:
                await asyncio.sleep(0.4 * (attempt + 1))
                continue
            break
        except Exception as e:
            last_error = e
            if kwargs_copy.get("parse_mode") and attempt == 0:
                kwargs_copy = dict(kwargs_copy)
                kwargs_copy.pop("parse_mode", None)
                continue
            break

    logger.warning("safe_edit_text failed after %s attempt(s): %s", retries + 1, last_error)
    return False


async def safe_send_message(bot, chat_id: int, text: str, *, retries: int = 2, **kwargs) -> bool:
    last_error: Exception | None = None
    kwargs_copy = dict(kwargs)
    for attempt in range(retries + 1):
        try:
            await bot.send_message(chat_id, text, **kwargs_copy)
            return True
        except TelegramNetworkError as e:
            last_error = e
            if attempt < retries:
                await asyncio.sleep(0.4 * (attempt + 1))
                continue
            break
        except TelegramBadRequest as e:
            if kwargs_copy.get("parse_mode"):
                kwargs_copy = dict(kwargs_copy)
                kwargs_copy.pop("parse_mode", None)
                try:
                    await bot.send_message(chat_id, text, parse_mode=None, **kwargs_copy)
                    return True
                except Exception as e2:
                    last_error = e2
                    break
            last_error = e
            break
        except Exception as e:
            last_error = e
            break
    logger.warning("safe_send_message failed: %s", last_error)
    return False


async def safe_callback_answer(callback, text: str = "", *, show_alert: bool = False) -> None:
    try:
        await callback.answer(text, show_alert=show_alert)
    except Exception as e:
        logger.debug("callback.answer failed: %s", e)

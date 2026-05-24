"""Inline-навигация: якорное сообщение и удаление ephemeral."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot

logger = logging.getLogger(__name__)


def ui_state(task_data: dict) -> dict[str, Any]:
    return task_data.setdefault("ui", {})


async def cleanup_ephemeral(bot: Bot, chat_id: int, task_data: dict) -> None:
    """Удаляет временные сообщения, накопленные в рамках задачи."""
    state = ui_state(task_data)
    for msg_id in state.get("ephemeral_ids", []):
        try:
            await bot.delete_message(chat_id, msg_id)
        except Exception:
            pass
    state["ephemeral_ids"] = []


def track_ephemeral(task_data: dict, message_id: int) -> None:
    ui_state(task_data).setdefault("ephemeral_ids", []).append(message_id)


async def delete_message_safe(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


class EditTarget:
    """Обёртка для edit_text по chat_id/message_id (якорное inline-сообщение)."""

    def __init__(self, bot: Bot, chat_id: int, message_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._message_id = message_id

    async def edit_text(self, text: str, **kwargs) -> None:
        await self._bot.edit_message_text(
            chat_id=self._chat_id,
            message_id=self._message_id,
            text=text,
            **kwargs,
        )

"""Единый прогресс-репортер для Telegram-сообщений."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import Any

from services import timing_stats
from utils.telegram_format import escape_html

logger = logging.getLogger(__name__)


def progress_bar(pct: float, width: int = 10) -> str:
    filled = round(max(0.0, min(pct, 1.0)) * width)
    return "█" * filled + "░" * (width - filled)


class ProgressReporter:
    """
    Обновляет одно сообщение: этап, подэтап, % и heartbeat при долгих операциях.
    Принимает Message или EditTarget (любой объект с edit_text).

    Для этапов без программного % (AI, GDrive, метаданные) передайте stage_key
    в set_stage — после N замеров появится предиктивный прогресс-бар.
    """

    HEARTBEAT_AFTER_SEC = 30.0

    def __init__(self, message: Any, *, title: str = "") -> None:
        self._message = message
        self._title = title
        self._stage = ""
        self._detail = ""
        self._subdetail = ""
        self._pct: float | None = None
        self._started = time.monotonic()
        self._stage_started = time.monotonic()
        self._done = asyncio.Event()
        self._ticker: asyncio.Task | None = None
        self._active_stage_key: str | None = None
        self._stage_context: dict = {}
        self._timing_stage_started = time.monotonic()
        self._timing_skipped = False

    async def __aenter__(self) -> ProgressReporter:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self._timing_skipped = True
        await self.stop()

    async def start(self, interval: float = 2.5) -> None:
        self._done.clear()
        self._ticker = asyncio.create_task(self._loop(interval))

    async def stop(self) -> None:
        self._record_current_stage_timing()
        self._done.set()
        if self._ticker:
            self._ticker.cancel()
            with suppress(asyncio.CancelledError):
                await self._ticker
            self._ticker = None

    def set_stage(
        self,
        stage: str,
        detail: str = "",
        *,
        subdetail: str = "",
        stage_key: str | None = None,
        context: dict | None = None,
    ) -> None:
        if stage_key is not None and stage_key != self._active_stage_key:
            if self._active_stage_key:
                self._record_current_stage_timing()
            self._active_stage_key = stage_key
            self._stage_context = dict(context or {})
            self._timing_stage_started = time.monotonic()
        elif context and self._active_stage_key:
            self._stage_context.update(context)

        self._stage = stage
        self._detail = detail
        self._subdetail = subdetail
        self._stage_started = time.monotonic()
        if stage_key is not None:
            self._pct = None

    def set_detail(self, detail: str, *, subdetail: str = "") -> None:
        self._detail = detail
        self._subdetail = subdetail

    def set_pct(self, pct: float | None) -> None:
        if pct is None:
            self._pct = None
        else:
            self._pct = max(0.0, min(pct, 0.99))

    def _timing_elapsed(self) -> float:
        if self._active_stage_key:
            return time.monotonic() - self._timing_stage_started
        return time.monotonic() - self._stage_started

    def _record_current_stage_timing(self) -> None:
        if self._timing_skipped or not self._active_stage_key:
            return
        duration = time.monotonic() - self._timing_stage_started
        timing_stats.record_sample(
            self._active_stage_key,
            duration,
            self._stage_context,
        )
        self._active_stage_key = None
        self._stage_context = {}

    def build_text(self) -> str:
        elapsed = time.monotonic() - self._started
        stage_elapsed = time.monotonic() - self._stage_started
        timing_elapsed = self._timing_elapsed()
        parts: list[str] = []
        if self._title:
            parts.append(escape_html(self._title))
        if self._stage:
            parts.append(f"<b>{escape_html(self._stage)}</b>")
        if self._detail:
            parts.append(f"▸ {escape_html(self._detail)}")
        if self._subdetail:
            parts.append(f"▸ {escape_html(self._subdetail)}")

        if self._pct is not None:
            bar = progress_bar(self._pct)
            parts.append(f"[{bar}] {self._pct * 100:.0f}%")
            parts.append(f"⏱ {elapsed:.0f}с")
        elif self._active_stage_key:
            prediction = timing_stats.get_prediction(self._active_stage_key, self._stage_context)
            if prediction and not prediction.collecting:
                pct = timing_stats.elapsed_ratio(timing_elapsed, prediction.duration_sec)
                bar = progress_bar(pct)
                parts.append(
                    timing_stats.format_timing_line(
                        timing_elapsed,
                        prediction,
                        bar=bar,
                        pct=pct,
                    )
                )
            else:
                parts.append(timing_stats.format_timing_line(timing_elapsed, prediction))
        else:
            parts.append(f"⏱ {elapsed:.0f}с")

        if self._stage and stage_elapsed >= self.HEARTBEAT_AFTER_SEC:
            parts.append(f"⏳ всё ещё: {self._stage} ({stage_elapsed:.0f}с)")
        return "\n".join(parts)

    async def push(self, *, parse_mode: str = "HTML") -> None:
        from bot.ui.telegram_safe import safe_edit_text

        await safe_edit_text(self._message, self.build_text(), parse_mode=parse_mode)

    async def _loop(self, interval: float) -> None:
        while not self._done.is_set():
            await asyncio.sleep(interval)
            if self._done.is_set():
                break
            await self.push()

    async def show_error(
        self,
        stage: str,
        message: str,
        technical: str = "",
        *,
        reply_markup=None,
    ) -> None:
        from bot.ui import screens
        from bot.ui.telegram_safe import safe_edit_text

        self._timing_skipped = True
        await self.stop()
        text = screens.error_text(stage, message, technical)
        logger.error("UI error [%s]: %s%s", stage, message, f" | {technical[:200]}" if technical else "")
        await safe_edit_text(
            self._message,
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )

    async def finish(self, text: str, **kwargs) -> None:
        from bot.ui.telegram_safe import safe_edit_text

        await self.stop()
        await safe_edit_text(self._message, text, **kwargs)

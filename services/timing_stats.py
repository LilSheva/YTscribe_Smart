"""
services/timing_stats.py — Сбор и предиктивная оценка длительности этапов.

До TIMING_MIN_SAMPLES измерений по stage_key показываем только секунды.
После — прогресс-бар на основе медианы последних TIMING_RECENT_K замеров.
"""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass

from core.config import TIMING_MIN_SAMPLES, TIMING_RECENT_K
from services import db

logger = logging.getLogger(__name__)

# Ключи этапов (стабильные идентификаторы для статистики)
STAGE_METADATA = "metadata"
STAGE_DOWNLOAD_M4A = "download_m4a"
STAGE_DOWNLOAD_MP4 = "download_mp4"
STAGE_TRANSCRIBE = "transcribe"
STAGE_LLM_VARIANTS = "llm_variants"
STAGE_LLM_ANALYZE = "llm_analyze"
STAGE_GDRIVE_MEDIA = "gdrive_media"
STAGE_GDRIVE_TRANSCRIPT = "gdrive_transcript"


@dataclass(frozen=True)
class TimingPrediction:
    duration_sec: float
    sample_count: int
    collecting: bool = False


def record_sample(stage_key: str, duration_sec: float, context: dict | None = None) -> None:
    """Сохраняет замер длительности этапа."""
    if duration_sec < 0.5:
        return
    try:
        db.insert_timing_sample(stage_key, duration_sec, context or {})
    except Exception as e:
        logger.warning(f"timing_stats: не удалось сохранить замер {stage_key}: {e}")


def get_prediction(stage_key: str, context: dict | None = None) -> TimingPrediction | None:
    """
    Возвращает предполагаемую длительность этапа.

    None — если замеров ещё нет (можно показывать только секунды).
    collecting=True — идёт набор до TIMING_MIN_SAMPLES.
    """
    total = db.count_timing_samples(stage_key)
    if total == 0:
        return None

    if total < TIMING_MIN_SAMPLES:
        return TimingPrediction(
            duration_sec=0.0,
            sample_count=total,
            collecting=True,
        )

    durations = _select_durations(stage_key, context)
    if len(durations) < TIMING_MIN_SAMPLES:
        durations = db.get_timing_durations(stage_key, limit=TIMING_RECENT_K)

    predicted = statistics.median(durations)
    return TimingPrediction(
        duration_sec=max(predicted, 1.0),
        sample_count=len(durations),
        collecting=False,
    )


def elapsed_ratio(elapsed_sec: float, predicted_sec: float) -> float:
    """Доля прогресса 0..0.95 по прошедшему времени."""
    if predicted_sec <= 0:
        return 0.0
    return min(max(elapsed_sec / predicted_sec, 0.0), 0.95)


def format_timing_line(
    elapsed_sec: float,
    prediction: TimingPrediction | None,
    *,
    bar: str | None = None,
    pct: float | None = None,
) -> str:
    """Строка времени для ProgressReporter."""
    elapsed = max(0.0, elapsed_sec)
    if prediction is None:
        return f"⏱ {elapsed:.0f}с"

    if prediction.collecting:
        return (
            f"⏱ {elapsed:.0f}с • сбор статистики "
            f"{prediction.sample_count}/{TIMING_MIN_SAMPLES}"
        )

    if bar is not None and pct is not None:
        remaining = max(prediction.duration_sec - elapsed, 0.0)
        return (
            f"[{bar}] {pct * 100:.0f}% • "
            f"{elapsed:.0f}с / ~{prediction.duration_sec:.0f}с"
            f" (~{remaining:.0f}с ост.)"
        )

    return f"⏱ {elapsed:.0f}с / ~{prediction.duration_sec:.0f}с"


def _select_durations(stage_key: str, context: dict | None) -> list[float]:
    """Последние K замеров с учётом похожего контекста (если возможно)."""
    recent = db.get_timing_samples(stage_key, limit=TIMING_RECENT_K * 4)
    if not context:
        return [s.duration_sec for s in recent[:TIMING_RECENT_K]]

    filtered = _filter_by_context(recent, context)
    if len(filtered) >= TIMING_MIN_SAMPLES:
        return [s.duration_sec for s in filtered[:TIMING_RECENT_K]]

    return [s.duration_sec for s in recent[:TIMING_RECENT_K]]


def _filter_by_context(samples: list[db.TimingSample], context: dict) -> list[db.TimingSample]:
    """Оставляет замеры с близким контекстом (размер файла, длина текста и т.д.)."""
    if "transcript_chars" in context:
        target = int(context["transcript_chars"])
        if target <= 0:
            return samples
        margin = max(target * 0.35, 500)
        matched = [
            s for s in samples
            if abs(int(s.context.get("transcript_chars", target)) - target) <= margin
        ]
        if matched:
            return matched

    if "file_size_mb" in context:
        target = float(context["file_size_mb"])
        if target <= 0:
            return samples
        margin = max(target * 0.4, 1.0)
        matched = [
            s for s in samples
            if abs(float(s.context.get("file_size_mb", target)) - target) <= margin
        ]
        if matched:
            return matched

    if "duration_sec" in context:
        target = int(context["duration_sec"])
        if target <= 0:
            return samples
        margin = max(target * 0.35, 60)
        matched = [
            s for s in samples
            if abs(int(s.context.get("duration_sec", target)) - target) <= margin
        ]
        if matched:
            return matched

    return samples

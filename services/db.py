"""
services/db.py — Слой доступа к данным YTscribe_Smart.

Схема:
  videos            — метаданные видео (дедупликация по video_id)
  processing_state  — чекпоинты обработки каждого видео
  analysis_results  — история LLM-ответов
  analysis_variants — кэшированные варианты анализа

Все публичные функции синхронные (вызываются через asyncio.to_thread при необходимости).
"""

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.config import DATA_DIR, ENABLE_HISTORY
from core.models import MediaTask

logger = logging.getLogger(__name__)

DB_PATH = DATA_DIR / "history.db"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"


# ─────────────────────────── dataclasses ────────────────────────────

@dataclass
class VideoEntry:
    video_id: str
    url: str
    title: str
    channel: str
    duration_sec: int
    upload_date: str = ""
    language: str = ""
    added_at: str = ""
    added_by_user_id: int = 0
    id: int = 0


@dataclass
class ProcessingState:
    video_id: str
    # транскрипт
    has_transcript: bool = False
    transcript_path: str = ""
    # GDrive — транскрипт
    gdrive_transcript_url: str = ""
    gdrive_transcript_synced_at: str = ""
    # LLM
    has_summary: bool = False
    llm_call_count: int = 0
    last_llm_prompt: str = ""
    last_llm_result_at: str = ""
    # GDrive — .md обновлён после последнего LLM
    gdrive_updated_after_llm: bool = False


@dataclass
class AnalysisResult:
    id: int
    video_id: str
    label: str
    prompt: str
    result: str
    created_at: str


# ─────────────────────────── connection ─────────────────────────────

def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


# ─────────────────────────── init / migrate ─────────────────────────

def init_db() -> None:
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS videos (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id        TEXT    NOT NULL UNIQUE,
                url             TEXT    NOT NULL,
                title           TEXT    NOT NULL,
                channel         TEXT    NOT NULL,
                duration_sec    INTEGER NOT NULL DEFAULT 0,
                upload_date     TEXT    NOT NULL DEFAULT '',
                language        TEXT    NOT NULL DEFAULT '',
                added_at        TEXT    NOT NULL,
                added_by_user_id INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS processing_state (
                video_id                    TEXT PRIMARY KEY
                                            REFERENCES videos(video_id) ON DELETE CASCADE,
                has_transcript              INTEGER NOT NULL DEFAULT 0,
                transcript_path             TEXT    NOT NULL DEFAULT '',
                gdrive_transcript_url       TEXT    NOT NULL DEFAULT '',
                gdrive_transcript_synced_at TEXT    NOT NULL DEFAULT '',
                has_summary                 INTEGER NOT NULL DEFAULT 0,
                llm_call_count              INTEGER NOT NULL DEFAULT 0,
                last_llm_prompt             TEXT    NOT NULL DEFAULT '',
                last_llm_result_at          TEXT    NOT NULL DEFAULT '',
                gdrive_updated_after_llm    INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS analysis_results (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id    TEXT    NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
                label       TEXT    NOT NULL,
                prompt      TEXT    NOT NULL,
                result      TEXT    NOT NULL,
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS analysis_variants (
                video_id        TEXT PRIMARY KEY
                                REFERENCES videos(video_id) ON DELETE CASCADE,
                variants_json   TEXT NOT NULL,
                created_at      TEXT NOT NULL
            );
        """)
        _migrate(c)


def _migrate(c: sqlite3.Connection) -> None:
    """Переносит данные из старых таблиц в новую схему."""
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    # Пересоздаём analysis_results если в ней старая колонка transcript_id
    if "analysis_results" in tables:
        cols = {r[1] for r in c.execute("PRAGMA table_info(analysis_results)").fetchall()}
        if "transcript_id" in cols:
            logger.info("DB migrate: пересоздаём analysis_results (transcript_id -> video_id)...")
            c.executescript("""
                ALTER TABLE analysis_results RENAME TO analysis_results_old;
                CREATE TABLE analysis_results (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id    TEXT    NOT NULL,
                    label       TEXT    NOT NULL,
                    prompt      TEXT    NOT NULL,
                    result      TEXT    NOT NULL,
                    created_at  TEXT    NOT NULL
                );
            """)

    # Пересоздаём analysis_variants если в ней старая колонка transcript_id
    if "analysis_variants" in tables:
        cols = {r[1] for r in c.execute("PRAGMA table_info(analysis_variants)").fetchall()}
        if "transcript_id" in cols:
            logger.info("DB migrate: пересоздаём analysis_variants (transcript_id -> video_id)...")
            c.executescript("""
                DROP TABLE analysis_variants;
                CREATE TABLE analysis_variants (
                    video_id        TEXT PRIMARY KEY,
                    variants_json   TEXT NOT NULL,
                    created_at      TEXT NOT NULL
                );
            """)

    if "transcripts" not in tables:
        return

    logger.info("DB migrate: переносим данные из 'transcripts' в новую схему...")
    rows = c.execute("SELECT * FROM transcripts").fetchall()
    migrated = 0
    for row in rows:
        r = dict(row)
        vid = r.get("video_id", "")
        if not vid:
            continue
        try:
            c.execute("""
                INSERT OR IGNORE INTO videos
                  (video_id, url, title, channel, duration_sec, added_at, added_by_user_id)
                VALUES (?,?,?,?,?,?,?)
            """, (vid, f"https://youtu.be/{vid}", r["title"], r.get("channel",""),
                  r.get("duration_sec", 0), r.get("created_at", _now()), r.get("user_id", 0)))

            c.execute("""
                INSERT OR IGNORE INTO processing_state
                  (video_id, has_transcript, transcript_path,
                   gdrive_transcript_url, gdrive_transcript_synced_at)
                VALUES (?,1,?,?,?)
            """, (vid, r.get("md_path",""), r.get("gdrive_url",""), r.get("created_at","")))
            migrated += 1
        except Exception as e:
            logger.warning(f"DB migrate: ошибка для {vid}: {e}")

    if migrated:
        logger.info(f"DB migrate: перенесено {migrated} записей. Старая таблица сохранена как 'transcripts_old'.")
        c.execute("ALTER TABLE transcripts RENAME TO transcripts_old")


# ─────────────────────────── helpers ────────────────────────────────

def _now() -> str:
    return datetime.now().isoformat()


def _row_to_video(row: sqlite3.Row) -> VideoEntry:
    d = dict(row)
    return VideoEntry(**{k: v for k, v in d.items() if k in VideoEntry.__dataclass_fields__})


def _row_to_state(row: sqlite3.Row) -> ProcessingState:
    d = dict(row)
    return ProcessingState(
        video_id=d["video_id"],
        has_transcript=bool(d["has_transcript"]),
        transcript_path=d["transcript_path"],
        gdrive_transcript_url=d["gdrive_transcript_url"],
        gdrive_transcript_synced_at=d["gdrive_transcript_synced_at"],
        has_summary=bool(d["has_summary"]),
        llm_call_count=d["llm_call_count"],
        last_llm_prompt=d["last_llm_prompt"],
        last_llm_result_at=d["last_llm_result_at"],
        gdrive_updated_after_llm=bool(d["gdrive_updated_after_llm"]),
    )


# ─────────────────────────── videos CRUD ────────────────────────────

def upsert_video(task: MediaTask, user_id: int) -> VideoEntry:
    """Создаёт или обновляет запись видео. Возвращает VideoEntry."""
    with _conn() as c:
        c.execute("""
            INSERT INTO videos (video_id, url, title, channel, duration_sec,
                                upload_date, language, added_at, added_by_user_id)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(video_id) DO UPDATE SET
                title=excluded.title,
                channel=excluded.channel,
                duration_sec=excluded.duration_sec
        """, (task.video_id, task.url, task.title, task.channel, task.duration_sec,
              task.upload_date, task.language, _now(), user_id))

        c.execute("""
            INSERT OR IGNORE INTO processing_state (video_id) VALUES (?)
        """, (task.video_id,))

        row = c.execute("SELECT * FROM videos WHERE video_id=?", (task.video_id,)).fetchone()
    return _row_to_video(row)


def get_video(video_id: str) -> VideoEntry | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM videos WHERE video_id=?", (video_id,)).fetchone()
    return _row_to_video(row) if row else None


def list_videos(user_id: int, limit: int = 50) -> list[VideoEntry]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM videos WHERE added_by_user_id=? ORDER BY added_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [_row_to_video(r) for r in rows]


# ─────────────────────────── processing_state ───────────────────────

def get_state(video_id: str) -> ProcessingState | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM processing_state WHERE video_id=?", (video_id,)).fetchone()
    return _row_to_state(row) if row else None


def set_transcript(video_id: str, transcript_path: str) -> None:
    with _conn() as c:
        c.execute("""
            UPDATE processing_state
            SET has_transcript=1, transcript_path=?, gdrive_updated_after_llm=0
            WHERE video_id=?
        """, (transcript_path, video_id))


def set_gdrive_transcript(video_id: str, url: str) -> None:
    with _conn() as c:
        c.execute("""
            UPDATE processing_state
            SET gdrive_transcript_url=?, gdrive_transcript_synced_at=?
            WHERE video_id=?
        """, (url, _now(), video_id))


def record_llm_call(video_id: str, prompt: str) -> None:
    with _conn() as c:
        c.execute("""
            UPDATE processing_state
            SET has_summary=1,
                llm_call_count=llm_call_count+1,
                last_llm_prompt=?,
                last_llm_result_at=?,
                gdrive_updated_after_llm=0
            WHERE video_id=?
        """, (prompt, _now(), video_id))


def set_gdrive_synced_after_llm(video_id: str) -> None:
    with _conn() as c:
        c.execute("""
            UPDATE processing_state
            SET gdrive_updated_after_llm=1, gdrive_transcript_synced_at=?
            WHERE video_id=?
        """, (_now(), video_id))


# ─────────────────────────── transcript text ────────────────────────

def get_transcript_text(video_id: str) -> str | None:
    state = get_state(video_id)
    if not state or not state.transcript_path:
        return None
    p = Path(state.transcript_path)
    return p.read_text(encoding="utf-8") if p.exists() else None


def save_transcript_file(task: MediaTask, text: str) -> Path:
    """Генерирует .md через md_generator, сохраняет в data/transcripts/, обновляет processing_state."""
    from utils.md_generator import generate_transcript_md
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    # md_generator пишет в TEMP_DIR; перемещаем в TRANSCRIPTS_DIR
    tmp_path = generate_transcript_md(task, text)
    safe = "".join(c for c in task.title if c not in '<>:"/\\|?*')[:80]
    final_path = TRANSCRIPTS_DIR / f"{task.video_id}_{safe}.md"
    tmp_path.replace(final_path)
    set_transcript(task.video_id, str(final_path))
    return final_path


def append_llm_to_file(video_id: str, label: str, prompt: str, result: str) -> None:
    """Дописывает LLM-ответ в .md файл транскрипта."""
    state = get_state(video_id)
    if not state or not state.transcript_path:
        return
    p = Path(state.transcript_path)
    if not p.exists():
        return
    section = f"\n\n---\n\n## AI: {label}\n\n**Промпт:** {prompt}\n\n{result}\n"
    with open(p, "a", encoding="utf-8") as f:
        f.write(section)


# ─────────────────────────── analysis_results ───────────────────────

def add_analysis_result(video_id: str, label: str, prompt: str, result: str) -> int:
    with _conn() as c:
        cur = c.execute("""
            INSERT INTO analysis_results (video_id, label, prompt, result, created_at)
            VALUES (?,?,?,?,?)
        """, (video_id, label, prompt, result, _now()))
        return cur.lastrowid


def get_analysis_results(video_id: str) -> list[AnalysisResult]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM analysis_results WHERE video_id=? ORDER BY created_at",
            (video_id,),
        ).fetchall()
    return [AnalysisResult(**dict(r)) for r in rows]


def get_analysis_result(result_id: int) -> AnalysisResult | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM analysis_results WHERE id=?", (result_id,)).fetchone()
    return AnalysisResult(**dict(row)) if row else None


# ─────────────────────────── analysis_variants ──────────────────────

def save_variants(video_id: str, variants: list[dict]) -> None:
    with _conn() as c:
        c.execute("""
            INSERT INTO analysis_variants (video_id, variants_json, created_at)
            VALUES (?,?,?)
            ON CONFLICT(video_id) DO UPDATE SET variants_json=excluded.variants_json, created_at=excluded.created_at
        """, (video_id, json.dumps(variants, ensure_ascii=False), _now()))


def get_variants(video_id: str) -> list[dict] | None:
    with _conn() as c:
        row = c.execute(
            "SELECT variants_json FROM analysis_variants WHERE video_id=?", (video_id,)
        ).fetchone()
    return json.loads(row["variants_json"]) if row else None


# ─────────────────────────── delete ─────────────────────────────────

def delete_video(video_id: str) -> None:
    state = get_state(video_id)
    if state and state.transcript_path:
        Path(state.transcript_path).unlink(missing_ok=True)
    with _conn() as c:
        c.execute("DELETE FROM videos WHERE video_id=?", (video_id,))


# ─────────────────────────── PKH API helpers ────────────────────────

def list_transcribed(limit: int = 200) -> list[dict]:
    """
    Возвращает все видео с транскриптом для PKH-поиска.
    Формат: [{video_id, title, channel, duration_sec, transcript_path,
              gdrive_url, has_summary, llm_call_count, added_at}]
    """
    with _conn() as c:
        rows = c.execute("""
            SELECT v.video_id, v.title, v.channel, v.duration_sec, v.added_at,
                   s.transcript_path, s.gdrive_transcript_url,
                   s.has_summary, s.llm_call_count
            FROM videos v
            JOIN processing_state s ON s.video_id = v.video_id
            WHERE s.has_transcript = 1
            ORDER BY v.added_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]

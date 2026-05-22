import sqlite3
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.config import DATA_DIR, ENABLE_HISTORY
from core.models import MediaTask

logger = logging.getLogger(__name__)

DB_PATH = DATA_DIR / "history.db"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"


@dataclass
class HistoryEntry:
    id: int
    user_id: int
    video_id: str
    title: str
    channel: str
    duration_sec: int
    gdrive_url: str
    md_path: str
    created_at: str


def _get_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transcripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                video_id TEXT NOT NULL,
                title TEXT NOT NULL,
                channel TEXT NOT NULL,
                duration_sec INTEGER NOT NULL,
                gdrive_url TEXT NOT NULL,
                md_path TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analysis_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transcript_id INTEGER NOT NULL,
                label TEXT NOT NULL,
                prompt TEXT NOT NULL,
                result TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(transcript_id) REFERENCES transcripts(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analysis_variants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transcript_id INTEGER NOT NULL UNIQUE,
                variants_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(transcript_id) REFERENCES transcripts(id)
            )
        """)


def add(user_id: int, task: MediaTask, gdrive_url: str, transcript: str) -> int:
    if not ENABLE_HISTORY:
        return -1
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_title = "".join(c for c in task.title if c not in '<>:"/\\|?*')[:80]
    md_filename = f"{task.video_id}_{safe_title}.md"
    md_path = TRANSCRIPTS_DIR / md_filename
    md_path.write_text(transcript, encoding="utf-8")
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO transcripts (user_id, video_id, title, channel, duration_sec, gdrive_url, md_path, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (user_id, task.video_id, task.title, task.channel, task.duration_sec, gdrive_url, str(md_path), datetime.now().isoformat()),
        )
        return cur.lastrowid


def list_by_user(user_id: int, limit: int = 10) -> list[HistoryEntry]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM transcripts WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [HistoryEntry(**dict(r)) for r in rows]


def get(entry_id: int) -> HistoryEntry | None:
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM transcripts WHERE id=?", (entry_id,)).fetchone()
    return HistoryEntry(**dict(row)) if row else None


def delete(entry_id: int) -> None:
    entry = get(entry_id)
    if entry:
        Path(entry.md_path).unlink(missing_ok=True)
        with _get_conn() as conn:
            conn.execute("DELETE FROM transcripts WHERE id=?", (entry_id,))


def get_transcript_text(entry: HistoryEntry) -> str | None:
    md_path = Path(entry.md_path)
    if md_path.exists():
        return md_path.read_text(encoding="utf-8")
    return None


def get_by_video_id(user_id: int, video_id: str) -> HistoryEntry | None:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM transcripts WHERE user_id=? AND video_id=? ORDER BY created_at DESC LIMIT 1",
            (user_id, video_id),
        ).fetchone()
    return HistoryEntry(**dict(row)) if row else None


def append_llm_result(entry: HistoryEntry, label: str, prompt: str, result: str) -> None:
    """Дописывает ответ LLM в .md файл транскрипта."""
    md_path = Path(entry.md_path)
    section = (
        f"\n\n---\n\n## AI: {label}\n\n"
        f"**Промпт:** {prompt}\n\n"
        f"{result}\n"
    )
    with open(md_path, "a", encoding="utf-8") as f:
        f.write(section)


def save_analysis_result(transcript_id: int, label: str, prompt: str, result: str) -> int:
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO analysis_results (transcript_id, label, prompt, result, created_at) VALUES (?,?,?,?,?)",
            (transcript_id, label, prompt, result, datetime.now().isoformat()),
        )
        return cur.lastrowid


def get_analysis_results(transcript_id: int) -> list[dict]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, label, prompt, result, created_at FROM analysis_results WHERE transcript_id=? ORDER BY created_at",
            (transcript_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_analysis_result(result_id: int) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM analysis_results WHERE id=?", (result_id,)).fetchone()
    return dict(row) if row else None


def save_variants(transcript_id: int, variants: list[dict]) -> None:
    """Сохраняет варианты анализа как JSON в отдельную таблицу."""
    import json
    with _get_conn() as conn:
        conn.execute("DELETE FROM analysis_variants WHERE transcript_id=?", (transcript_id,))
        conn.execute(
            "INSERT INTO analysis_variants (transcript_id, variants_json, created_at) VALUES (?,?,?)",
            (transcript_id, json.dumps(variants, ensure_ascii=False), datetime.now().isoformat()),
        )


def get_variants(transcript_id: int) -> list[dict] | None:
    import json
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT variants_json FROM analysis_variants WHERE transcript_id=?",
            (transcript_id,),
        ).fetchone()
    return json.loads(row["variants_json"]) if row else None


def update_gdrive_url(entry_id: int, new_url: str) -> None:
    with _get_conn() as conn:
        conn.execute("UPDATE transcripts SET gdrive_url=? WHERE id=?", (new_url, entry_id))

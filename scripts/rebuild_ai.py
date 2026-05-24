"""
Полный сброс AI-данных и перегенерация авто-саммари.

Сохраняет: videos, транскрипты (.md), метаданные processing_state.
Удаляет: analysis_results, analysis_variants, флаги has_summary.
Опционально: чистит LLM-кеш, снимает AI-секции из .md, заново прогоняет саммари.

Примеры:
  python scripts/rebuild_ai.py --dry-run
  python scripts/rebuild_ai.py --yes
  python scripts/rebuild_ai.py --yes --skip-llm          # только сброс, без LLM
  python scripts/rebuild_ai.py --yes --clear-cache
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import AUTO_LLM_SUMMARY, ENABLE_LLM, LLM_MODEL
from core.logger import setup_logging
from services import db
from services import llm_cache
from services.auto_summary import run_auto_summary_if_enabled
from services.transcript_json import sync_ai_from_db


def _print_plan(stats: dict[str, int], *, skip_llm: bool, clear_cache: bool) -> None:
    print("=== План пересборки AI ===")
    print(f"  Видео с транскриптом:     {stats['transcribed']}")
    print(f"  LLM-ответов в БД:         {stats['analysis_results']}")
    print(f"  Кэш вариантов:            {stats['analysis_variants']}")
    print(f"  С has_summary=1:          {stats['with_summary']}")
    print()
    print("  Будет удалено из БД:      analysis_results, analysis_variants")
    print("  Сброс флагов:             has_summary, llm_call_count, gdrive_updated_after_llm")
    print("  JSON:                     sync ai_analysis из БД после сброса")
    if clear_cache:
        print("  LLM cache:                полная очистка")
    if skip_llm:
        print("  LLM:                      пропуск (--skip-llm)")
    elif not ENABLE_LLM or not AUTO_LLM_SUMMARY:
        print("  LLM:                      ВНИМАНИЕ — ENABLE_LLM или AUTO_LLM_SUMMARY выключены")
    else:
        print(f"  LLM:                      авто-саммари для всех ({LLM_MODEL} / user prefs)")
    print()


async def _run_summaries(
    records: list[dict],
    *,
    default_user_id: int,
    delay_sec: float,
) -> tuple[int, int]:
    ok = failed = 0
    total = len(records)

    for i, rec in enumerate(records, start=1):
        video_id = rec["video_id"]
        title = (rec.get("title") or video_id)[:50]
        user_id = int(rec.get("added_by_user_id") or default_user_id or 0)

        text = db.get_transcript_text(video_id)
        if not text:
            print(f"  [{i}/{total}] SKIP {title} — нет текста транскрипта")
            failed += 1
            continue

        entry = db.get_video(video_id)
        if not entry:
            failed += 1
            continue

        task = db.video_entry_to_task(entry)
        print(f"  [{i}/{total}] {title}…", flush=True)

        try:
            done = await run_auto_summary_if_enabled(task, text, user_id, message=None)
            if done:
                ok += 1
            else:
                failed += 1
                print(f"         -> не выполнено (LLM отключён или пустой текст)")
        except Exception as e:
            failed += 1
            print(f"         -> ошибка: {e}")

        if delay_sec > 0 and i < total:
            await asyncio.sleep(delay_sec)

    return ok, failed


async def _main_async(args: argparse.Namespace) -> int:
    stats = db.count_ai_data()
    records = db.list_transcribed(limit=args.limit)

    _print_plan(stats, skip_llm=args.skip_llm, clear_cache=args.clear_cache)

    if not records:
        print("Нет видео с транскриптом — нечего делать.")
        return 0

    if args.dry_run:
        print("Dry-run: изменений нет.")
        return 0

    if not args.yes:
        print("Добавьте --yes для выполнения (операция необратима для AI-истории).")
        return 2

    print("1/4 — синхронизирую JSON (очистка ai_analysis в файлах)…")
    records_pre = db.list_transcribed(limit=args.limit)
    sync_ok = sync_fail = 0
    for rec in records_pre:
        vid = rec["video_id"]
        path = __import__("services.transcript_paths", fromlist=["find_transcript_file"]).find_transcript_file(vid)
        if path and path.suffix.lower() == ".json":
            from utils.json_format import build_document, load_document, save_document
            from services import db as _db

            entry = _db.get_video(vid)
            if entry:
                task = _db.video_entry_to_task(entry)
                doc = load_document(path)
                text = doc.get("transcript", {}).get("text", "")
                processed = doc.get("transcript", {}).get("processed_at")
                sync = doc.get("sync", {})
                new_doc = build_document(
                    task,
                    text,
                    added_by_user_id=entry.added_by_user_id,
                    added_at=entry.added_at,
                    processed_at=processed,
                    ai_analysis=[],
                    sync=sync,
                )
                save_document(path, new_doc)
                sync_ok += 1
            else:
                sync_fail += 1
        else:
            sync_fail += 1
    print(f"      JSON: ok={sync_ok}, пропуск={sync_fail}")

    print("2/4 — сбрасываю AI в базе…")
    removed = db.reset_all_ai_data()
    print(
        f"      удалено ответов: {removed['analysis_results']}, "
        f"вариантов: {removed['analysis_variants']}"
    )

    if args.clear_cache:
        print("3/4 — очищаю LLM cache…")
        n = llm_cache.clear_cache()
        print(f"      удалено файлов кеша: {n}")
    else:
        print("3/4 — LLM cache не трогаем (добавьте --clear-cache чтобы очистить)")

    if args.skip_llm:
        print("4/4 — пропуск LLM (--skip-llm)")
        print()
        print("Готово. Запустите позже:")
        print("  python scripts/rebuild_ai.py --yes --only-llm")
        return 0

    if not ENABLE_LLM or not AUTO_LLM_SUMMARY:
        print("4/4 — LLM пропущен: включите ENABLE_LLM и AUTO_LLM_SUMMARY в .env")
        return 1

    print(f"4/4 — генерирую авто-саммари ({len(records)} видео)…")
    ok, failed = await _run_summaries(
        records,
        default_user_id=args.user_id,
        delay_sec=args.delay,
    )
    print()
    print(f"=== Готово ===")
    print(f"  Саммари OK: {ok}")
    print(f"  Ошибок:     {failed}")
    print()
    print("Дальше: ☁️ GDrive sync → Исправить sync (или python scripts/gdrive_sync_cli.py repair)")
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Сброс AI и перегенерация саммари")
    parser.add_argument("--dry-run", action="store_true", help="только план, без изменений")
    parser.add_argument("--yes", action="store_true", help="подтвердить выполнение")
    parser.add_argument("--skip-llm", action="store_true", help="только сброс БД и .md")
    parser.add_argument(
        "--only-llm",
        action="store_true",
        help="только LLM (без сброса; для продолжения после --skip-llm)",
    )
    parser.add_argument("--clear-cache", action="store_true", help="очистить data/llm_cache")
    parser.add_argument("--user-id", type=int, default=0, help="user_id для модели LLM по умолчанию")
    parser.add_argument("--limit", type=int, default=10_000)
    parser.add_argument("--delay", type=float, default=1.0, help="пауза между LLM-запросами (сек)")
    args = parser.parse_args()

    setup_logging()
    db.init_db()

    if args.only_llm:
        records = db.list_transcribed(limit=args.limit)
        if not records:
            print("Нет транскриптов.")
            return 0
        if not ENABLE_LLM or not AUTO_LLM_SUMMARY:
            print("ENABLE_LLM / AUTO_LLM_SUMMARY выключены.")
            return 1
        print(f"Генерирую саммари для {len(records)} видео…")
        ok, failed = asyncio.run(
            _run_summaries(records, default_user_id=args.user_id, delay_sec=args.delay)
        )
        print(f"OK: {ok}, failed: {failed}")
        return 0 if failed == 0 else 1

    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())

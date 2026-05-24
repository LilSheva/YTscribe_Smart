"""
Legacy: нормализация .md транскриптов (до JSON v1.0).

Актуальный формат — JSON. Для миграции используйте scripts/migrate_md_to_json.py.

Примеры:
  python scripts/legacy/normalize_md.py --audit
  python scripts/legacy/normalize_md.py --yes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEGACY = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(LEGACY) not in sys.path:
    sys.path.insert(0, str(LEGACY))

from core.logger import setup_logging
from md_sync import audit_md_structure, normalize_all_transcripts
from services import db


def main() -> int:
    parser = argparse.ArgumentParser(description="Legacy: нормализация .md")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument("--limit", type=int, default=10_000)
    args = parser.parse_args()

    setup_logging()
    db.init_db()

    if args.audit or args.dry_run:
        bad = audit_md_structure(limit=args.limit)
        total = len(db.list_transcribed(limit=args.limit))
        print(f"=== Аудит .md ({total} файлов) ===")
        if not bad:
            print("Все файлы соответствуют канонической структуре.")
            return 0
        print(f"Проблемных: {len(bad)}")
        for video_id, title, issues in bad[:20]:
            t = (title or video_id)[:45]
            print(f"  • {t}: {', '.join(issues)}")
        if len(bad) > 20:
            print(f"  … и ещё {len(bad) - 20}")
        if args.dry_run:
            print("\nDry-run: файлы не изменены.")
        return 1

    if not args.yes:
        print("Добавьте --yes для записи или --audit для проверки.")
        return 2

    ok, failed, remaining = normalize_all_transcripts(
        limit=args.limit,
        include_ai=not args.no_ai,
    )
    print(f"=== Нормализация завершена ===\n  OK: {ok}\n  Ошибок: {failed}")
    if remaining:
        print(f"  Замечания после записи: {len(remaining)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

"""
Миграция .md → JSON v1.0.

Примеры:
  python scripts/migrate_md_to_json.py --dry-run
  python scripts/migrate_md_to_json.py --yes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.logger import setup_logging
from services import db
from services.transcript_migrate import migrate_one


def main() -> int:
    parser = argparse.ArgumentParser(description="Миграция MD → JSON")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--limit", type=int, default=10_000)
    args = parser.parse_args()

    setup_logging()
    db.init_db()

    records = db.list_transcribed(limit=args.limit)
    ok = fail = skip = 0

    print(f"=== Миграция MD -> JSON ({len(records)} видео) ===")
    if not args.yes and not args.dry_run:
        print("Добавьте --yes для выполнения или --dry-run для плана.")
        return 2

    for rec in records:
        vid = rec["video_id"]
        title = (rec.get("title") or vid)[:45]
        success, msg = migrate_one(vid, execute=args.yes)
        if success:
            if msg == "уже JSON":
                skip += 1
            else:
                ok += 1
                if args.yes or args.dry_run:
                    print(f"OK  {title}: {msg}")
        else:
            fail += 1
            print(f"ERR {title}: {msg}")

    print(f"\nГотово: ok={ok} skip={skip} fail={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

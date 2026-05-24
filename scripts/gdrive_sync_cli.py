"""CLI для аудита/восстановления GDrive без запуска бота."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.logger import setup_logging
from services.db import init_db
from services import db
from services import gdrive_sync


def main() -> int:
    parser = argparse.ArgumentParser(description="YTscribe GDrive sync")
    parser.add_argument(
        "command",
        choices=("audit", "repair", "repair-dry", "migrate"),
        help="audit / repair / repair-dry / migrate (local: копировать .md в GDRIVE_LOCAL_DIR)",
    )
    parser.add_argument("--no-drive-check", action="store_true", help="не проверять 404 на Drive")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()

    setup_logging()
    init_db()

    check_drive = not args.no_drive_check

    if args.command == "migrate":
        from services import gdrive
        from services.transcript_paths import ensure_in_sync_folder

        if not gdrive.use_local_transcript_sync():
            print("migrate: только для GDRIVE_MODE=local")
            return 1
        records = db.list_transcribed(limit=args.limit)
        ok = fail = 0
        for rec in records:
            vid = rec["video_id"]
            title = (rec.get("title") or vid)[:45]
            p = ensure_in_sync_folder(vid)
            if p:
                ok += 1
                print(f"OK  {title} -> {p.parent}")
            else:
                fail += 1
                print(f"ERR {title}")
        print(f"\nГотово: ok={ok} fail={fail}")
        return 0 if fail == 0 else 1

    if args.command == "audit":
        report = gdrive_sync.audit(check_drive=check_drive, limit=args.limit)
        print(gdrive_sync.format_audit_report(report, html=False))
        return 0 if report.issue_count == 0 else 1

    dry_run = args.command == "repair-dry"
    batch = gdrive_sync.repair_all(dry_run=dry_run, check_drive=check_drive, limit=args.limit)
    if dry_run:
        report = gdrive_sync.audit(check_drive=check_drive, limit=args.limit)
        print(gdrive_sync.format_audit_report(report, html=False))
        print()
        print(gdrive_sync.format_repair_report(batch, html=False))
        return 0

    print(gdrive_sync.format_repair_report(batch, html=False))
    return 0 if batch.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

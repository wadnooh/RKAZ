#!/usr/bin/env python3
"""استعادة طارئة لحفظات ركاز — للاستخدام التشغيلي على السيرفر فقط (بدون واجهة).

أمثلة على VPS:
  cd /opt/rekaz
  sudo -u rekazapp /opt/rekaz/.venv/bin/python tools/restore_backup.py --list
  sudo -u rekazapp /opt/rekaz/.venv/bin/python tools/restore_backup.py --rel 2026/08/03/HHMMSS__auto__...
  sudo -u rekazapp /opt/rekaz/.venv/bin/python tools/restore_backup.py --s3-latest --yes
  sudo -u rekazapp /opt/rekaz/.venv/bin/python tools/restore_backup.py --s3-key rekaz-backups/....zip --yes
  sudo -u rekazapp /opt/rekaz/.venv/bin/python tools/restore_backup.py --export-zip /tmp/rekaz-latest.zip
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


def main() -> int:
    parser = argparse.ArgumentParser(description="استعادة/تصدير حفظات ركاز (CLI تشغيلي)")
    parser.add_argument("--list", action="store_true", help="عرض أحدث الحفظات المحلية")
    parser.add_argument("--list-s3", action="store_true", help="عرض أحدث الحفظات على S3")
    parser.add_argument("--rel", help="مسار نسبي لحفظة محلية للاستعادة")
    parser.add_argument("--s3-key", help="مفتاح كائن S3 (.zip) للاستعادة")
    parser.add_argument("--s3-latest", action="store_true", help="استعادة أحدث حفظة من S3")
    parser.add_argument("--export-zip", help="تصدير أحدث حفظة محلية إلى مسار ZIP")
    parser.add_argument("--yes", action="store_true", help="تأكيد الاستعادة دون سؤال تفاعلي")
    parser.add_argument("--limit", type=int, default=20, help="عدد العناصر عند العرض")
    args = parser.parse_args()

    _load_env_file(Path("/etc/rekaz.env"))
    if not os.environ.get("RAKAZ_DATA_DIR"):
        # تطوير محلي
        os.environ.setdefault("RAKAZ_DATA_DIR", str(ROOT / "instance"))

    from webapp import backup as backup_svc
    from webapp import db

    db.init_db()

    if args.list:
        items = backup_svc.list_backups(limit=args.limit)
        if not items:
            print("لا توجد حفظات محلية.")
            return 0
        for item in items:
            prog = item.get("progress") or {}
            print(
                f"{item.get('_rel')} | {item.get('created_at')} | "
                f"{item.get('purpose')} | tickets={prog.get('tickets_total', 0)}"
            )
        return 0

    if args.list_s3:
        items = backup_svc.list_s3_backups(limit=args.limit)
        if not items:
            print("لا توجد حفظات على S3 أو S3 غير مضبوط.")
            return 0
        for item in items:
            print(f"{item.get('key')} | {item.get('last_modified')} | {item.get('size')}")
        return 0

    if args.export_zip:
        latest = backup_svc.latest_backup(purpose="auto") or backup_svc.latest_backup()
        if not latest:
            print("لا توجد حفظات لتصديرها.", file=sys.stderr)
            return 1
        zip_path = backup_svc.build_backup_zip(latest["_rel"])
        dest = Path(args.export_zip)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(zip_path.read_bytes())
        print(f"تم التصدير: {dest} ← {latest.get('_rel')}")
        return 0

    restoring = bool(args.rel or args.s3_key or args.s3_latest)
    if not restoring:
        parser.print_help()
        return 2

    if not args.yes:
        print("أضف --yes لتأكيد الاستعادة (يُنشأ حفظ أمان أولاً).", file=sys.stderr)
        return 3

    if args.rel:
        result = backup_svc.restore_backup(args.rel, user_name="CLI استعادة")
        db.ensure_schema()
        print(f"تمت الاستعادة المحلية: {args.rel}")
        print(f"حفظة الأمان: {(result.get('safety') or {}).get('id')}")
        return 0

    key = args.s3_key
    if args.s3_latest:
        remote = backup_svc.list_s3_backups(limit=1)
        if not remote:
            print("لا توجد حفظات على S3.", file=sys.stderr)
            return 1
        key = remote[0]["key"]
    result = backup_svc.restore_from_s3(key, user_name="CLI استعادة←S3")
    db.ensure_schema()
    print(f"تمت الاستعادة من S3: {key}")
    print(f"الحفظة: {(result.get('imported') or {}).get('id')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

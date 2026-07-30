"""Smoke checks for S3 backup/restore selection and seed detection (no AWS required)."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    # --- sort by LastModified (not alphabetical key) ---
    items = [
        {"key": "rekaz-backups/2026-01-01T00-00-00__old.zip", "last_modified": "2026-01-01T00:00:00+00:00"},
        {"key": "rekaz-backups/2026-07-30T21-41-42__new.zip", "last_modified": "2026-07-30T21:41:45+00:00"},
        {"key": "rekaz-backups/2026-03-15T12-00-00__mid.zip", "last_modified": "2026-03-15T12:00:00+00:00"},
    ]
    items.sort(key=lambda x: x.get("last_modified") or "", reverse=True)
    assert items[0]["key"].endswith("__new.zip"), items[0]
    print("OK sort_newest_by_last_modified")

    td = tempfile.mkdtemp(prefix="rekaz-smoke-backup-")
    try:
        os.environ["RAKAZ_DATA_DIR"] = td
        os.environ.pop("RENDER", None)
        os.environ.pop("RAKAZ_CLOUD", None)
        os.environ.pop("AWS_S3_BUCKET", None)
        os.environ["RAKAZ_SEED_DEMO"] = "1"

        import importlib

        import webapp.db as db
        import webapp.backup as backup

        importlib.reload(db)
        importlib.reload(backup)

        db.init_db()
        assert backup.local_db_is_blank_or_seed(), "seed-only DB must look blank/seed"
        print("OK seed_detected_as_blank")

        conn = db.connect()
        conn.execute(
            "INSERT INTO audit_log(user_name, action, entity, entity_id, details) VALUES (?,?,?,?,?)",
            ("admin", "تعديل", "عطل", 1, "smoke"),
        )
        conn.commit()
        conn.close()
        assert not backup.local_db_is_blank_or_seed(), "audit trail means real user data"
        print("OK audit_marks_real_data")

        # ephemeral cloud delay cap
        os.environ["TRIAL_MODE"] = "1"
        os.environ["RENDER"] = "1"
        os.environ["AUTO_BACKUP_ACTIVITY_MINUTES"] = "30"
        importlib.reload(backup)
        delay = backup.activity_backup_delay_seconds()
        assert delay <= 90, delay
        print(f"OK trial_delay_capped_at_{delay}s")
    finally:
        shutil.rmtree(td, ignore_errors=True)
        for k in ("RAKAZ_DATA_DIR", "RAKAZ_SEED_DEMO", "TRIAL_MODE", "RENDER", "AUTO_BACKUP_ACTIVITY_MINUTES"):
            os.environ.pop(k, None)

    print("ALL_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""إعادة تعيين أجهزة المبرمج الموثوقة (استعادة عند القفل).

الاستخدام على VPS (SSH فقط):
  cd /opt/rekaz
  sudo -u rekazapp /opt/rekaz/.venv/bin/python tools/programmer_reset_devices.py --yes

بعدها سجّل الجهاز الرئيسي من جديد عبر /admin/programmer/device
مع PROGRAMMER_BOOTSTRAP_CODE من /etc/rekaz.env
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in __import__("os").environ:
            __import__("os").environ[key] = val


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset programmer trusted devices")
    parser.add_argument("--yes", action="store_true", help="Confirm deletion")
    args = parser.parse_args()
    if not args.yes:
        print("Refusing without --yes (safety). Example:", file=sys.stderr)
        print("  python tools/programmer_reset_devices.py --yes", file=sys.stderr)
        return 2

    _load_env_file(Path("/etc/rekaz.env"))
    from webapp import db

    n = db.clear_programmer_devices()
    print(f"Cleared {n} programmer device row(s).")
    print("Re-register main device at /admin/programmer/device with BOOTSTRAP_CODE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""يولّد رمز موافقة لمرة واحدة لتعديلات المبرمج من جهاز غير رئيسي.

الاستخدام على VPS:
  cd /opt/rekaz
  sudo -u rekazapp /opt/rekaz/.venv/bin/python tools/programmer_approve.py

الرمز صالح ~10 دقائق ويُستهلك بعد أول استخدام ناجح في الواجهة.
"""

from __future__ import annotations

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
    _load_env_file(Path("/etc/rekaz.env"))
    from webapp import db
    from webapp import programmer_guard as prog_guard

    db.ensure_schema()
    if not prog_guard.change_pin():
        print("WARNING: PROGRAMMER_CHANGE_PIN is not set in the environment.", file=sys.stderr)
    code, expires = prog_guard.create_approve_code_record()
    print("Programmer approval code (one-time):")
    print(code)
    print(f"Expires (UTC): {expires.strftime('%Y-%m-%d %H:%M:%S')}")
    print("Enter it on: /admin/programmer/verify together with password + CHANGE_PIN.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
